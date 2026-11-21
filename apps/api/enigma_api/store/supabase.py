"""Supabase store: PostgREST + RPC with the SERVER-ONLY service-role key.

The service role bypasses RLS, so every caller must already have resolved the tenant (membership / token scope); the SQL functions then
re-check ids against the rows. This class is exercised in CI only with a mocked transport (request shape + error mapping). Behaviour
against a live Supabase project is BLOCKED_EXTERNAL_SETUP (see docs/TEST_REPORT.md); the SQL it calls IS tested on a local Postgres.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx

from .base import Row, Store

PAGE = 1000      # Supabase's default PostgREST max-rows


class StoreUnavailable(RuntimeError):
    """Database unreachable or returned an unexpected status. Routes map this to 503; messages never include row data."""


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


class SupabaseStore(Store):
    def __init__(self, url: str, service_key: str, transport: httpx.BaseTransport | None = None, timeout: float = 8.0):
        self._c = httpx.Client(base_url=url.rstrip("/") + "/rest/v1", timeout=timeout, transport=transport,
                               headers={"apikey": service_key, "Authorization": f"Bearer {service_key}", "Content-Type": "application/json"})

    # ---- plumbing
    def _req(self, method: str, path: str, *, params: dict | None = None, body: Any = None, prefer: str | None = None, headers: dict | None = None):
        h = dict(headers or {})
        if prefer:
            h["Prefer"] = prefer
        try:
            r = self._c.request(method, path, params=params, content=None if body is None else json.dumps(body, default=_iso), headers=h)
        except httpx.HTTPError as e:
            raise StoreUnavailable(f"database request failed: {type(e).__name__}") from None
        if r.status_code >= 400:
            raise StoreUnavailable(f"database returned {r.status_code}")      # body deliberately not included
        return r.json() if r.content else None

    def _rpc(self, fn: str, args: dict) -> Any:
        return self._req("POST", f"/rpc/{fn}", body=args)

    def _one(self, table: str, **eq: str) -> Row | None:
        rows = self._req("GET", f"/{table}", params={"select": "*", **{k: f"eq.{v}" for k, v in eq.items()}, "limit": "1"})
        return rows[0] if rows else None

    def _many(self, table: str, params: dict, limit: int) -> list[Row]:
        out: list[Row] = []
        while len(out) < limit:
            lo = len(out)
            hi = min(lo + PAGE, limit) - 1
            page = self._req("GET", f"/{table}", params={"select": "*", **params}, headers={"Range-Unit": "items", "Range": f"{lo}-{hi}"})
            out += page or []
            if not page or len(page) < hi - lo + 1:
                break
        return out[:limit]

    def _insert(self, table: str, row: Row) -> Row:
        return self._req("POST", f"/{table}", body=row, prefer="return=representation")[0]

    # ---- tenancy
    def create_workspace(self, user_id, name, slug):
        return self._rpc("enigma_create_workspace", {"p_user": user_id, "p_name": name, "p_slug": slug})

    def list_workspaces(self, user_id):
        ms = self._many("memberships", {"user_id": f"eq.{user_id}"}, 200)
        if not ms:
            return []
        ids = ",".join(m["workspace_id"] for m in ms)
        roles = {m["workspace_id"]: m["role"] for m in ms}
        return [{**w, "role": roles[w["id"]]} for w in self._many("workspaces", {"id": f"in.({ids})"}, 200)]

    def get_role(self, user_id, workspace_id):
        m = self._one("memberships", user_id=user_id, workspace_id=workspace_id)
        return m["role"] if m else None

    def create_project(self, workspace_id, name, user_id):
        return self._insert("projects", {"workspace_id": workspace_id, "name": name, "created_by": user_id})

    def list_projects(self, workspace_id):
        return self._many("projects", {"workspace_id": f"eq.{workspace_id}", "archived_at": "is.null", "order": "created_at.asc"}, 200)

    def get_project(self, project_id):
        return self._one("projects", id=project_id)

    # ---- runtimes
    def create_runtime(self, workspace_id, project_id, name, is_mock, user_id):
        return self._insert("runtimes", {"workspace_id": workspace_id, "project_id": project_id, "name": name, "is_mock": is_mock, "created_by": user_id})

    def get_runtime(self, runtime_id):
        return self._one("runtimes", id=runtime_id)

    def list_runtimes(self, project_id):
        return self._many("runtimes", {"project_id": f"eq.{project_id}", "disabled_at": "is.null", "order": "created_at.asc"}, 200)

    def add_credential(self, runtime, prefix, secret_hash, expires_at, user_id, rotated_from=None):
        return self._insert("runtime_credentials", {"workspace_id": runtime["workspace_id"], "project_id": runtime["project_id"], "runtime_id": runtime["id"],
                                                    "prefix": prefix, "secret_hash": secret_hash, "expires_at": _iso(expires_at), "created_by": user_id,
                                                    "rotated_from": rotated_from})

    def get_credential_by_prefix(self, prefix):
        return self._one("runtime_credentials", prefix=prefix)

    def revoke_credentials(self, runtime_id):
        rows = self._req("PATCH", "/runtime_credentials", params={"runtime_id": f"eq.{runtime_id}", "revoked_at": "is.null"},
                         body={"revoked_at": datetime.now().astimezone().isoformat()}, prefer="return=representation")
        return len(rows or [])

    def revoke_credential(self, credential_id):
        self._req("PATCH", "/runtime_credentials", params={"id": f"eq.{credential_id}", "revoked_at": "is.null"},
                  body={"revoked_at": datetime.now().astimezone().isoformat()})

    def list_credentials(self, runtime_id):
        cols = "id,workspace_id,project_id,runtime_id,prefix,scopes,created_by,created_at,expires_at,revoked_at,last_used_at"
        return self._req("GET", "/runtime_credentials", params={"select": cols, "runtime_id": f"eq.{runtime_id}", "order": "created_at.desc"}) or []

    def touch_credential(self, credential_id):
        self._req("PATCH", "/runtime_credentials", params={"id": f"eq.{credential_id}"}, body={"last_used_at": datetime.now().astimezone().isoformat()})

    def disable_runtime(self, runtime_id):
        self._req("PATCH", "/runtimes", params={"id": f"eq.{runtime_id}"}, body={"disabled_at": datetime.now().astimezone().isoformat()})

    def register_runtime(self, runtime_id, info):
        self._req("PATCH", "/runtimes", params={"id": f"eq.{runtime_id}"}, body={
            "served_model": info["served_model"], "tp": info["tp"], "pp": info["pp"], "max_model_len": info["max_model_len"],
            "capabilities": {"gpus": info["gpus"], **info["capabilities"]}, "binary_identity": info["sdk"],
            "registered_at": datetime.now().astimezone().isoformat()})
        self._rpc("enigma_heartbeat", {"p_runtime": runtime_id})
        return self._rpc("enigma_start_run", {"p_runtime": runtime_id, "p_meta": {k: info[k] for k in ("tp", "pp", "max_model_len")} | {"model": info["served_model"]}})

    def heartbeat(self, runtime_id):
        return self._rpc("enigma_heartbeat", {"p_runtime": runtime_id})

    # ---- chat
    def create_conversation(self, workspace_id, project_id, user_id, title, runtime_id):
        row = {"workspace_id": workspace_id, "project_id": project_id, "created_by": user_id, "runtime_id": runtime_id}
        if title:
            row["title"] = title
        return self._insert("conversations", row)

    def get_conversation(self, conversation_id):
        return self._one("conversations", id=conversation_id)

    def list_conversations(self, project_id, limit=50):
        return self._many("conversations", {"project_id": f"eq.{project_id}", "order": "updated_at.desc"}, limit)

    def list_messages(self, conversation_id, limit=200):
        rows = self._many("messages", {"conversation_id": f"eq.{conversation_id}", "order": "ordinal.desc"}, limit)
        return list(reversed(rows))

    # ---- jobs
    def submit_job(self, workspace_id, conversation_id, runtime_id, user_id, idem, content, params, char_budget):
        return self._rpc("enigma_submit_job", {"p_workspace": workspace_id, "p_conversation": conversation_id, "p_runtime": runtime_id, "p_user": user_id,
                                               "p_idem": idem, "p_content": content, "p_params": params, "p_char_budget": char_budget})

    def claim_job(self, runtime_id, owner, lease_seconds):
        rows = self._rpc("enigma_claim_job", {"p_runtime": runtime_id, "p_owner": owner, "p_lease_seconds": lease_seconds})
        return rows[0] if rows else None

    def append_events(self, runtime_id, job_id, owner, events, lease_seconds):
        return self._rpc("enigma_append_events", {"p_runtime": runtime_id, "p_job": job_id, "p_owner": owner, "p_events": events, "p_lease_seconds": lease_seconds})

    def complete_job(self, runtime_id, job_id, owner, final_seq, text_sha256, result):
        return self._rpc("enigma_complete_job", {"p_runtime": runtime_id, "p_job": job_id, "p_owner": owner, "p_final_seq": final_seq,
                                                 "p_text_sha256": text_sha256, "p_result": result})

    def fail_job(self, runtime_id, job_id, owner, code, message, retryable):
        return self._rpc("enigma_fail_job", {"p_runtime": runtime_id, "p_job": job_id, "p_owner": owner, "p_code": code, "p_message": message, "p_retryable": retryable})

    def ack_cancel(self, runtime_id, job_id, owner):
        return self._rpc("enigma_ack_cancel", {"p_runtime": runtime_id, "p_job": job_id, "p_owner": owner})

    def request_cancel(self, workspace_id, job_id):
        return self._rpc("enigma_request_cancel", {"p_workspace": workspace_id, "p_job": job_id})

    def expire_jobs(self, runtime_id=None):
        self._rpc("enigma_expire_jobs", {"p_runtime": runtime_id})

    def get_job(self, job_id):
        self.expire_jobs()
        return self._one("inference_jobs", id=job_id)

    def list_events(self, job_id, after_seq, limit):
        return self._many("inference_events", {"job_id": f"eq.{job_id}", "event_seq": f"gt.{after_seq}", "order": "event_seq.asc"}, limit)

    # ---- telemetry
    def ingest_telemetry(self, runtime_id, batch_id, run_id, gpu, vllm):
        return self._rpc("enigma_ingest_telemetry", {"p_runtime": runtime_id, "p_batch_id": batch_id, "p_run": run_id, "p_gpu": gpu, "p_vllm": vllm})

    def _window(self, table, project_id, runtime_id, since, until, limit):
        params = {"project_id": f"eq.{project_id}", "and": f"(captured_at.gte.{_iso(since)},captured_at.lte.{_iso(until)})", "order": "captured_at.asc"}
        if runtime_id:
            params["runtime_id"] = f"eq.{runtime_id}"
        return self._many(table, params, limit)

    def query_gpu(self, project_id, runtime_id, since, until, limit):
        return self._window("gpu_samples", project_id, runtime_id, since, until, limit)

    def query_vllm(self, project_id, runtime_id, since, until, limit):
        return self._window("vllm_metric_samples", project_id, runtime_id, since, until, limit)

    # ---- runs
    def reap_runs(self, stale_seconds=90):
        return self._rpc("enigma_reap_runs", {"p_stale_seconds": stale_seconds})

    def list_runs(self, project_id, limit=50):
        self.reap_runs()
        return self._many("inference_runs", {"project_id": f"eq.{project_id}", "order": "started_at.desc"}, limit)

    def get_run(self, run_id):
        return self._one("inference_runs", id=run_id)

    def prune(self, event_hours=48, gpu_days=14, vllm_days=14):
        return self._rpc("enigma_prune", {"p_event_hours": event_hours, "p_gpu_days": gpu_days, "p_vllm_days": vllm_days})

    def audit(self, workspace_id, actor, action, target_type=None, target_id=None, meta=None):
        self._req("POST", "/audit_events", body={"workspace_id": workspace_id, "actor": actor, "action": action, "target_type": target_type,
                                                 "target_id": target_id, "meta": meta or {}})
