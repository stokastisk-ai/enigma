"""PostgreSQL store (AWS RDS / any vanilla PostgreSQL 14+) using psycopg 3 and the portable schema in infra/aws/sql/.

Security model: the API connects as a LOGIN role that is a member of ``enigma_app`` (least privilege, no RLS bypass needed because no RLS
exists in the portable schema). Authorisation is done by the FastAPI layer before any call; the SQL functions then re-check tenant /
runtime ids against the rows, exactly like the Supabase store. All values are bound parameters; identifiers are fixed constants in this
file -- nothing user-controlled is ever interpolated into SQL.

Lambda notes: one connection per execution environment (a Lambda environment serves one request at a time), opened lazily, autocommit so
every statement / SQL function call is its own atomic transaction, reconnect once on a dropped connection, hard statement timeout.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .base import Row, Store
from .supabase import StoreUnavailable      # same 503 mapping in the routes

log = logging.getLogger("enigma.postgres")

IDLE_PROBE_S = 30.0
_CRED_PUBLIC = "id,workspace_id,project_id,runtime_id,prefix,scopes,created_by,created_at,expires_at,revoked_at,last_used_at"


def _clean(v: Any) -> Any:
    """UUID -> str so rows look like PostgREST rows (the routes/Pydantic models expect strings)."""
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


class PostgresStore(Store):
    def __init__(self, dsn: str, *, statement_timeout_ms: int = 8000, connect_timeout_s: int = 5, connect=None):
        if not dsn:
            raise ValueError("ENIGMA_DB_DSN is required for the postgres store")
        self._dsn, self._stmt_ms, self._conn_s = dsn, statement_timeout_ms, connect_timeout_s
        self._connect = connect or psycopg.connect
        self._conn: psycopg.Connection | None = None
        self._lock = threading.Lock()
        self._last_used = 0.0

    # ---------------------------------------------------------------- plumbing
    def _open(self) -> psycopg.Connection:
        return self._connect(self._dsn, autocommit=True, row_factory=dict_row, connect_timeout=self._conn_s,
                             options=f"-c statement_timeout={int(self._stmt_ms)} -c idle_in_transaction_session_timeout=10000")

    def _ensure(self) -> psycopg.Connection:
        """Return a healthy connection. Opening is retried once (nothing has been sent yet). A connection idle longer than
        IDLE_PROBE_S (a frozen Lambda environment may hold a dead socket) is probed with a harmless `select 1` and replaced if dead."""
        now = time.monotonic()
        if self._conn is not None and not self._conn.closed and now - self._last_used > IDLE_PROBE_S:
            try:
                self._conn.execute("select 1")
            except psycopg.Error:
                self._conn = None
        if self._conn is None or self._conn.closed:
            for attempt in (1, 2):
                try:
                    self._conn = self._open()
                    break
                except psycopg.Error as e:
                    if attempt == 2:
                        raise StoreUnavailable(f"database unavailable: {type(e).__name__}") from None
        return self._conn

    def _run(self, sql: str, args: tuple | list | None = None) -> list[Row] | None:
        """Execute ONE statement. Never retried after it has been sent: a write may or may not have committed, and replaying a
        non-idempotent statement (insert) could duplicate it. Callers/clients retry at the API level (idempotency keys)."""
        with self._lock:
            conn = self._ensure()
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, args)
                    rows = cur.fetchall() if cur.description else None
                self._last_used = time.monotonic()
                return [_clean(r) for r in rows] if rows is not None else None
            except psycopg.OperationalError as e:
                self._conn = None                                   # connection state unknown: discard it
                raise StoreUnavailable(f"database unavailable: {type(e).__name__}") from None
            except psycopg.Error as e:
                # SQLSTATE only: the message can contain row values (unique-violation detail etc.)
                raise StoreUnavailable(f"database error: {getattr(e, 'sqlstate', None) or type(e).__name__}") from None

    def _one(self, sql: str, args=None) -> Row | None:
        rows = self._run(sql, args)
        return rows[0] if rows else None

    def _fn(self, name: str, sig: str, args: tuple) -> Any:
        """Call a jsonb/scalar-returning enigma_* function. `name` and `sig` are literals from this file."""
        return self._run(f"select {name}({sig}) as r", args)[0]["r"]

    # ---------------------------------------------------------------- tenancy
    def create_workspace(self, user_id, name, slug):
        return self._fn("enigma_create_workspace", "%s,%s,%s", (user_id, name, slug))

    def list_workspaces(self, user_id):
        return self._run("select w.*, m.role from workspaces w join memberships m on m.workspace_id = w.id where m.user_id = %s limit 200", (user_id,))

    def get_role(self, user_id, workspace_id):
        r = self._one("select role from memberships where user_id = %s and workspace_id = %s::uuid", (user_id, workspace_id))
        return r["role"] if r else None

    def create_project(self, workspace_id, name, user_id):
        return self._one("insert into projects (workspace_id, name, created_by) values (%s::uuid, %s, %s) returning *", (workspace_id, name, user_id))

    def list_projects(self, workspace_id):
        return self._run("select * from projects where workspace_id = %s::uuid and archived_at is null order by created_at limit 200", (workspace_id,))

    def get_project(self, project_id):
        return self._one("select * from projects where id = %s::uuid", (project_id,))

    # ---------------------------------------------------------------- runtimes / credentials
    def create_runtime(self, workspace_id, project_id, name, is_mock, user_id):
        return self._one("insert into runtimes (workspace_id, project_id, name, is_mock, created_by) values (%s::uuid, %s::uuid, %s, %s, %s) returning *",
                         (workspace_id, project_id, name, is_mock, user_id))

    def get_runtime(self, runtime_id):
        return self._one("select * from runtimes where id = %s::uuid", (runtime_id,))

    def list_runtimes(self, project_id):
        return self._run("select * from runtimes where project_id = %s::uuid and disabled_at is null order by created_at limit 200", (project_id,))

    def add_credential(self, runtime, prefix, secret_hash, expires_at, user_id, rotated_from=None):
        return self._one("insert into runtime_credentials (workspace_id, project_id, runtime_id, prefix, secret_hash, expires_at, created_by, rotated_from) "
                         "values (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s::uuid) returning *",
                         (runtime["workspace_id"], runtime["project_id"], runtime["id"], prefix, secret_hash, expires_at, user_id, rotated_from))

    def get_credential_by_prefix(self, prefix):
        return self._one("select * from runtime_credentials where prefix = %s", (prefix,))

    def revoke_credentials(self, runtime_id):
        return len(self._run("update runtime_credentials set revoked_at = now() where runtime_id = %s::uuid and revoked_at is null returning id", (runtime_id,)))

    def revoke_credential(self, credential_id):
        self._run("update runtime_credentials set revoked_at = now() where id = %s::uuid and revoked_at is null", (credential_id,))

    def list_credentials(self, runtime_id):
        return self._run(f"select {_CRED_PUBLIC} from runtime_credentials where runtime_id = %s::uuid order by created_at desc limit 200", (runtime_id,))

    def touch_credential(self, credential_id):
        self._run("update runtime_credentials set last_used_at = now() where id = %s::uuid", (credential_id,))

    def disable_runtime(self, runtime_id):
        self._run("update runtimes set disabled_at = now() where id = %s::uuid", (runtime_id,))

    def register_runtime(self, runtime_id, info):
        caps = {"gpus": info["gpus"], **info["capabilities"]}
        self._run("update runtimes set served_model = %s, tp = %s, pp = %s, max_model_len = %s, capabilities = %s, binary_identity = %s, registered_at = now() "
                  "where id = %s::uuid", (info["served_model"], info["tp"], info["pp"], info["max_model_len"], Jsonb(caps), Jsonb(info["sdk"]), runtime_id))
        self._fn("enigma_heartbeat", "%s::uuid", (runtime_id,))
        meta = {k: info[k] for k in ("tp", "pp", "max_model_len")} | {"model": info["served_model"]}
        return self._fn("enigma_start_run", "%s::uuid, %s", (runtime_id, Jsonb(meta)))

    def heartbeat(self, runtime_id):
        return self._fn("enigma_heartbeat", "%s::uuid", (runtime_id,))

    # ---------------------------------------------------------------- chat
    def create_conversation(self, workspace_id, project_id, user_id, title, runtime_id):
        if title:
            return self._one("insert into conversations (workspace_id, project_id, created_by, runtime_id, title) values (%s::uuid, %s::uuid, %s, %s::uuid, %s) returning *",
                             (workspace_id, project_id, user_id, runtime_id, title))
        return self._one("insert into conversations (workspace_id, project_id, created_by, runtime_id) values (%s::uuid, %s::uuid, %s, %s::uuid) returning *",
                         (workspace_id, project_id, user_id, runtime_id))

    def get_conversation(self, conversation_id):
        return self._one("select * from conversations where id = %s::uuid", (conversation_id,))

    def list_conversations(self, project_id, limit=50):
        return self._run("select * from conversations where project_id = %s::uuid order by updated_at desc limit %s", (project_id, int(limit)))

    def list_messages(self, conversation_id, limit=200):
        rows = self._run("select * from messages where conversation_id = %s::uuid order by ordinal desc limit %s", (conversation_id, int(limit)))
        return list(reversed(rows))

    # ---------------------------------------------------------------- jobs (the SQL functions are the source of truth)
    def submit_job(self, workspace_id, conversation_id, runtime_id, user_id, idem, content, params, char_budget):
        return self._fn("enigma_submit_job", "%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s",
                        (workspace_id, conversation_id, runtime_id, user_id, idem, content, Jsonb(params), int(char_budget)))

    def claim_job(self, runtime_id, owner, lease_seconds):
        return self._one("select * from enigma_claim_job(%s::uuid, %s, %s)", (runtime_id, owner, int(lease_seconds)))

    def append_events(self, runtime_id, job_id, owner, events, lease_seconds):
        return self._fn("enigma_append_events", "%s::uuid,%s::uuid,%s,%s,%s", (runtime_id, job_id, owner, Jsonb(events), int(lease_seconds)))

    def complete_job(self, runtime_id, job_id, owner, final_seq, text_sha256, result):
        return self._fn("enigma_complete_job", "%s::uuid,%s::uuid,%s,%s,%s,%s", (runtime_id, job_id, owner, int(final_seq), text_sha256, Jsonb(result)))

    def fail_job(self, runtime_id, job_id, owner, code, message, retryable):
        return self._fn("enigma_fail_job", "%s::uuid,%s::uuid,%s,%s,%s,%s", (runtime_id, job_id, owner, code, message, bool(retryable)))

    def ack_cancel(self, runtime_id, job_id, owner):
        return self._fn("enigma_ack_cancel", "%s::uuid,%s::uuid,%s", (runtime_id, job_id, owner))

    def request_cancel(self, workspace_id, job_id):
        return self._fn("enigma_request_cancel", "%s::uuid,%s::uuid", (workspace_id, job_id))

    def expire_jobs(self, runtime_id=None):
        self._run("select enigma_expire_jobs(%s::uuid)", (runtime_id,))

    def get_job(self, job_id):
        self.expire_jobs()
        return self._one("select * from inference_jobs where id = %s::uuid", (job_id,))

    def list_events(self, job_id, after_seq, limit):
        return self._run("select * from inference_events where job_id = %s::uuid and event_seq > %s order by event_seq limit %s", (job_id, int(after_seq), int(limit)))

    # ---------------------------------------------------------------- telemetry
    def ingest_telemetry(self, runtime_id, batch_id, run_id, gpu, vllm):
        return self._fn("enigma_ingest_telemetry", "%s::uuid,%s,%s::uuid,%s,%s", (runtime_id, batch_id, run_id, Jsonb(gpu), Jsonb(vllm)))

    def _window(self, table: str, project_id, runtime_id, since: datetime, until: datetime, limit: int):
        assert table in ("gpu_samples", "vllm_metric_samples")           # fixed identifiers only
        sql = f"select * from {table} where project_id = %s::uuid and captured_at >= %s and captured_at <= %s"
        args: list[Any] = [project_id, since, until]
        if runtime_id:
            sql += " and runtime_id = %s::uuid"
            args.append(runtime_id)
        return self._run(sql + " order by captured_at limit %s", args + [int(limit)])

    def query_gpu(self, project_id, runtime_id, since, until, limit):
        return self._window("gpu_samples", project_id, runtime_id, since, until, limit)

    def query_vllm(self, project_id, runtime_id, since, until, limit):
        return self._window("vllm_metric_samples", project_id, runtime_id, since, until, limit)

    # ---------------------------------------------------------------- runs / housekeeping
    def reap_runs(self, stale_seconds=90):
        return self._fn("enigma_reap_runs", "%s", (int(stale_seconds),))

    def list_runs(self, project_id, limit=50):
        self.reap_runs()
        return self._run("select * from inference_runs where project_id = %s::uuid order by started_at desc limit %s", (project_id, int(limit)))

    def get_run(self, run_id):
        return self._one("select * from inference_runs where id = %s::uuid", (run_id,))

    def ready(self):
        self._run("select 1")                                       # opens/validates the connection; raises StoreUnavailable (SQLSTATE only) on failure

    def prune(self, event_hours=48, gpu_days=14, vllm_days=14):
        return self._fn("enigma_prune", "%s,%s,%s", (int(event_hours), int(gpu_days), int(vllm_days)))

    def audit(self, workspace_id, actor, action, target_type=None, target_id=None, meta=None):
        self._run("insert into audit_events (workspace_id, actor, action, target_type, target_id, meta) values (%s::uuid, %s, %s, %s, %s, %s)",
                  (workspace_id, actor, action, target_type, target_id, Jsonb(meta or {})))
