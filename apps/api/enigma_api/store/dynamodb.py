"""DynamoDB store (Track B). Single table, NO global secondary indexes, only strongly consistent reads.

STATUS: implemented and tested against moto (an in-process emulator) only. NOT deployed; no AWS call is made by the tests. The PostgreSQL
store (store/postgres.py) remains the reference implementation and is untouched until this one passes the same behavioural suite on a
real table. See docs/AWS_DYNAMODB_REAL_PARITY.md for the parity gate and the known differences listed at the bottom of this docstring.

Key design (PK / SK are strings)
  canonical rows   PK=<TYPE>#<id>            SK="#"                      WS, PRJ, RT, CRED, CONV, JOB, RUN, SLUG#<slug>, CREDPFX#<prefix>, IDEM#<ws>#<key>
  collections      PK=<parent>               SK=<CHILD>#<sortable>#<id>  value {ref: id}; read with a strongly consistent Query, then BatchGetItem
  messages/events  PK=CONV#<id> / JOB#<id>   SK=MSG#<n:012d> / EVT#<seq:010d>
  telemetry        PK=GPU#<project> / VLLM#<project>   SK=<captured_at>#<runtime>[#<gpu>]
Every mutation of a job is a conditional write on its version counter ``_ver`` (optimistic concurrency): that is what makes a claim atomic
(one winner), events strictly ordered with no gaps/duplicates, and completion happen exactly once. Multi-item changes use TransactWriteItems.
Per-runtime / per-user submit caps are serialised with the same trick on a ``_sv`` counter (a lock item), because a count-then-insert is not atomic.
Timestamps come from an injectable clock (UTC, fixed-width ISO strings so they sort lexicographically).

Known differences from the SQL implementation (documented, not hidden)
  * Time is the caller's clock, not the database's ``now()``: Lambda clock skew applies to leases (seconds), unlike a single Postgres clock.
  * ``expire_jobs(None)`` (global sweep) is a no-op; expiry is applied per runtime on claim and per job on read, which is what the API uses.
  * Telemetry de-duplication matches ``ON CONFLICT DO NOTHING`` via a consistent pre-read; concurrent batches racing on identical keys may double-count.
  * Retention: items carry a ``ttl`` (DynamoDB deletes within ~48 h); ``prune()`` additionally scans (bounded) for exact-age deletion.
  * No row-level security exists. Isolation is application-layer, exactly as with the PostgreSQL store (browsers never reach the table).
"""
from __future__ import annotations

import copy
import hashlib
import random
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .base import Row, Store
from .memory import ACTIVE, CAPS, ROLE_RANK, TERMINAL
from .supabase import StoreUnavailable      # same 503 mapping in the routes

_SER, _DE = TypeSerializer(), TypeDeserializer()
_TS = "%Y-%m-%dT%H:%M:%S.%fZ"
_NULL_T = {"NULL": True}
_NOT_PARSED = {"emitted_at"}                 # client-supplied strings are returned untouched
_THROTTLE = {"ProvisionedThroughputExceededException", "ThrottlingException", "RequestLimitExceeded", "InternalServerError", "ServiceUnavailable"}


class _Conflict(Exception):
    """A TransactWriteItems was cancelled. ``reasons`` is the per-item list of codes (None for items that were fine)."""

    def __init__(self, reasons: list[str | None]):
        super().__init__("transaction cancelled")
        self.reasons = reasons


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS)


def _dt(s: Any) -> Any:
    if isinstance(s, datetime) or s is None:
        return s
    return datetime.strptime(s, _TS).replace(tzinfo=timezone.utc) if isinstance(s, str) and s.endswith("Z") else datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _aware(v: Any) -> datetime:
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def _enc(v: Any) -> Any:
    """Python -> DynamoDB-friendly python (datetime->str, float->Decimal)."""
    if isinstance(v, datetime):
        return _ts(v)
    if isinstance(v, float):
        return Decimal(repr(v))
    if isinstance(v, dict):
        return {k: _enc(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_enc(x) for x in v]
    return v


def _dec(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() and v.as_tuple().exponent >= 0 else float(v)
    if isinstance(v, dict):
        return {k: _dec(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_dec(x) for x in v]
    return v


def _marshal(d: dict) -> dict:
    return {k: _SER.serialize(_enc(v)) for k, v in d.items()}


def _row(item: dict | None) -> Row | None:
    if not item:
        return None
    d = {k: _dec(_DE.deserialize(v)) for k, v in item.items() if k not in ("PK", "SK") and not k.startswith("_") and k != "ttl"}
    for k, v in list(d.items()):
        if k.endswith("_at") and k not in _NOT_PARSED and isinstance(v, str):
            d[k] = _dt(v)
    return d


def _key(pk: str, sk: str = "#") -> dict:
    return {"PK": {"S": pk}, "SK": {"S": sk}}


def _ex(text: str, vals: dict | None = None) -> dict:
    """ExpressionAttributeNames/Values for every '#name' / ':nullT' token in ``text`` (several expressions may be concatenated)."""
    names = {t: t[1:] for t in set(re.findall(r"#\w+", text))}
    out: dict = {"ExpressionAttributeNames": names} if names else {}
    values = _marshal(vals) if vals else {}
    if ":nullT" in text:
        values[":nullT"] = {"S": "NULL"}                  # attribute_type(x, :nullT) tests for the DynamoDB NULL type
    if values:
        out["ExpressionAttributeValues"] = values
    return out


def _isnull(attr: str) -> str:
    return f"(attribute_not_exists(#{attr}) OR attribute_type(#{attr}, :nullT))"


class DynamoStore(Store):
    def __init__(self, table: str, *, client: Any = None, region: str | None = None, clock: Callable[[], datetime] | None = None,
                 event_ttl_h: int = 48, telemetry_ttl_d: int = 14, max_retries: int = 8, scan_page_cap: int = 50):
        if not table:
            raise ValueError("ENIGMA_DDB_TABLE is required for the dynamodb store")
        self.table = table
        self.c = client or boto3.client("dynamodb", region_name=region, config=Config(retries={"max_attempts": 4, "mode": "standard"},
                                                                                   connect_timeout=2, read_timeout=6))
        self.now: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))
        self._ev_ttl_s, self._tm_ttl_s = event_ttl_h * 3600, telemetry_ttl_d * 86400
        self._retries, self._scan_cap = max_retries, scan_page_cap

    # ------------------------------------------------------------------ plumbing
    def _call(self, op: str, **kw):
        delay = 0.01
        for attempt in range(1, 6):
            try:
                return getattr(self.c, op)(**kw)
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code == "TransactionCanceledException":
                    raise _Conflict([(r or {}).get("Code") if (r or {}).get("Code") != "None" else None for r in e.response.get("CancellationReasons", [])]) from None
                if code == "ConditionalCheckFailedException":
                    raise _Conflict(["ConditionalCheckFailed"]) from None
                if code in _THROTTLE and attempt < 5:
                    time.sleep(delay + random.random() * delay)
                    delay *= 2
                    continue
                raise StoreUnavailable(f"database error: {code or type(e).__name__}") from None     # code only: never row data
            except BotoCoreError as e:
                if attempt < 3 and op in ("get_item", "query", "batch_get_item"):      # reads are safe to repeat; writes are not
                    time.sleep(delay)
                    continue
                raise StoreUnavailable(f"database unavailable: {type(e).__name__}") from None
        raise StoreUnavailable("database throttled")

    def _get(self, pk: str, sk: str = "#") -> Row | None:
        return _row(self._call("get_item", TableName=self.table, Key=_key(pk, sk), ConsistentRead=True).get("Item"))

    def _raw(self, pk: str, sk: str = "#") -> dict | None:
        return self._call("get_item", TableName=self.table, Key=_key(pk, sk), ConsistentRead=True).get("Item")

    def _query(self, pk: str, *, begins: str | None = None, between: tuple[str, str] | None = None, gt: str | None = None, forward: bool = True,
               limit: int | None = None, filt: str | None = None, fvals: dict | None = None) -> list[dict]:
        out: list[dict] = []
        kce, vals = "#PK = :pk", {":pk": pk}
        if begins is not None:
            kce += " AND begins_with(#SK, :b)"; vals[":b"] = begins
        elif between is not None:
            kce += " AND #SK BETWEEN :lo AND :hi"; vals[":lo"], vals[":hi"] = between
        elif gt is not None:
            kce += " AND #SK > :gt"; vals[":gt"] = gt
        expr = kce + (" " + filt if filt else "")
        names = {t: t[1:] for t in set(re.findall(r"#\w+", expr))}
        start = None
        while True:
            kw: dict = dict(TableName=self.table, KeyConditionExpression=kce, ExpressionAttributeNames=names, ConsistentRead=True,
                            ScanIndexForward=forward, ExpressionAttributeValues=_marshal({**vals, **(fvals or {})}))
            if filt:
                kw["FilterExpression"] = filt
            if start:
                kw["ExclusiveStartKey"] = start
            if limit is not None and not filt:
                kw["Limit"] = limit - len(out)
            r = self._call("query", **kw)
            out.extend(r.get("Items", []))
            start = r.get("LastEvaluatedKey")
            if not start or (limit is not None and len(out) >= limit):
                break
        return out[:limit] if limit is not None else out

    def _batch_get(self, keys: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        """Consistent BatchGetItem for any number of keys. UnprocessedKeys (returned under throttling) are retried with backoff; if work is
        still left after the retry budget this RAISES instead of returning a partial result (a silently truncated list is worse than an error)."""
        found: dict[tuple[str, str], dict] = {}
        uniq = list(dict.fromkeys(keys))
        for i in range(0, len(uniq), 100):
            req = {self.table: {"Keys": [_key(*k) for k in uniq[i:i + 100]], "ConsistentRead": True}}
            delay = 0.005
            for attempt in range(12):
                r = self._call("batch_get_item", RequestItems=req)
                for it in r.get("Responses", {}).get(self.table, []):
                    found[(it["PK"]["S"], it["SK"]["S"])] = it
                req = r.get("UnprocessedKeys") or {}
                if not req:
                    break
                time.sleep(min(delay, 0.2) * (1 + random.random()))
                delay *= 2
            else:
                raise StoreUnavailable("database throttled: batch read incomplete")
        return found

    def _refs(self, pointers: list[dict], prefix: str) -> list[dict]:
        """Resolve pointer items (value attr `ref`) to their canonical raw items, preserving pointer order."""
        keys = [(f"{prefix}{p['ref']['S']}", "#") for p in pointers]
        got = self._batch_get(keys)
        return [got[k] for k in keys if k in got]

    def _tx(self, ops: list[dict]) -> None:
        for attempt in range(self._retries):
            try:
                self._call("transact_write_items", TransactItems=ops)
                return
            except _Conflict as c:
                if any(r == "TransactionConflict" for r in c.reasons) and attempt < self._retries - 1:
                    time.sleep(0.005 * (attempt + 1) * (1 + random.random()))
                    continue
                raise

    # op builders
    def _put(self, item: dict, cond: str | None = None, cvals: dict | None = None) -> dict:
        op: dict = {"TableName": self.table, "Item": _marshal(item)}
        if cond:
            op["ConditionExpression"] = cond
            op.update(_ex(cond, cvals))
        return {"Put": op}

    def _upd(self, pk: str, sk: str, expr: str, vals: dict | None = None, cond: str | None = None) -> dict:
        op: dict = {"TableName": self.table, "Key": _key(pk, sk), "UpdateExpression": expr}
        if cond:
            op["ConditionExpression"] = cond
        op.update(_ex(expr + " " + (cond or ""), vals))
        return {"Update": op}

    def _update_item(self, pk: str, sk: str, expr: str, vals: dict | None = None, cond: str | None = None) -> bool:
        """Single conditional UpdateItem. Returns False when the condition failed."""
        op = self._upd(pk, sk, expr, vals, cond)["Update"]
        try:
            self._call("update_item", **op)
            return True
        except _Conflict:
            return False

    def _del(self, pk: str, sk: str) -> dict:
        return {"Delete": {"TableName": self.table, "Key": _key(pk, sk)}}

    def _id(self) -> str:
        return str(uuid.uuid4())

    # ------------------------------------------------------------------ tenancy
    def create_workspace(self, user_id, name, slug):
        now = self.now()
        ws_id, pr_id = self._id(), self._id()
        ws = {"id": ws_id, "name": name, "slug": slug, "created_by": user_id, "created_at": now}
        pr = {"id": pr_id, "workspace_id": ws_id, "name": "Default", "created_by": user_id, "created_at": now, "archived_at": None}
        ops = [
            self._put({"PK": f"SLUG#{slug}", "SK": "#", "workspace_id": ws_id}, "attribute_not_exists(PK)"),                    # 0: slug uniqueness
            self._put({"PK": f"WS#{ws_id}", "SK": "#", **ws}),
            self._put({"PK": f"WS#{ws_id}", "SK": f"MEM#{user_id}", "workspace_id": ws_id, "user_id": user_id, "role": "owner", "created_at": now}),
            self._put({"PK": f"USER#{user_id}", "SK": f"WS#{ws_id}", "ref": ws_id, "role": "owner"}),
            self._put({"PK": f"PRJ#{pr_id}", "SK": "#", **pr}),
            self._put({"PK": f"WS#{ws_id}", "SK": f"PRJ#{_ts(now)}#{pr_id}", "ref": pr_id}),
            self._upd(f"USER#{user_id}", "OWNED", "SET #n = if_not_exists(#n, :zero) + :one", {":zero": 0, ":one": 1, ":cap": CAPS_OWNER},
                      "attribute_not_exists(#n) OR #n < :cap"),                                                                  # 6: owner cap (atomic)
            self._put({"PK": f"AUDIT#{ws_id}", "SK": f"{_ts(now)}#{self._id()}", "workspace_id": ws_id, "actor": user_id, "action": "workspace.create",
                       "target_type": "workspace", "target_id": ws_id, "meta": {}, "at": now}),
        ]
        try:
            self._tx(ops)
        except _Conflict as c:
            if c.reasons and len(c.reasons) > 0 and c.reasons[0] == "ConditionalCheckFailed":
                return {"error": "slug_taken"}
            if len(c.reasons) > 6 and c.reasons[6] == "ConditionalCheckFailed":
                return {"error": "workspace_limit"}
            raise StoreUnavailable("database error: TransactionCanceled") from None
        return {"ok": True, "workspace": copy.deepcopy(ws), "project": copy.deepcopy(pr)}

    def add_member(self, workspace_id: str, user_id: str, role: str) -> None:        # test/dev helper (invites are post-MVP)
        now = self.now()
        self._tx([self._put({"PK": f"WS#{workspace_id}", "SK": f"MEM#{user_id}", "workspace_id": workspace_id, "user_id": user_id, "role": role, "created_at": now}),
                  self._put({"PK": f"USER#{user_id}", "SK": f"WS#{workspace_id}", "ref": workspace_id, "role": role})])

    def list_workspaces(self, user_id):
        ptrs = self._query(f"USER#{user_id}", begins="WS#")
        roles = {p["ref"]["S"]: p["role"]["S"] for p in ptrs}
        rows = [_row(i) for i in self._refs(ptrs, "WS#")]
        return sorted(({**r, "role": roles[r["id"]]} for r in rows), key=lambda r: r["created_at"])

    def get_role(self, user_id, workspace_id):
        it = self._raw(f"WS#{workspace_id}", f"MEM#{user_id}")
        return it["role"]["S"] if it else None

    def create_project(self, workspace_id, name, user_id):
        now, pid = self.now(), self._id()
        pr = {"id": pid, "workspace_id": workspace_id, "name": name, "created_by": user_id, "created_at": now, "archived_at": None}
        self._tx([self._put({"PK": f"PRJ#{pid}", "SK": "#", **pr}), self._put({"PK": f"WS#{workspace_id}", "SK": f"PRJ#{_ts(now)}#{pid}", "ref": pid})])
        return copy.deepcopy(pr)

    def list_projects(self, workspace_id):
        rows = [_row(i) for i in self._refs(self._query(f"WS#{workspace_id}", begins="PRJ#"), "PRJ#")]
        return [r for r in rows if not r["archived_at"]]

    def get_project(self, project_id):
        return self._get(f"PRJ#{project_id}")

    # ------------------------------------------------------------------ runtimes / credentials
    def create_runtime(self, workspace_id, project_id, name, is_mock, user_id):
        p = self.get_project(project_id)
        if not p or p["workspace_id"] != workspace_id:
            raise ValueError("project_not_in_workspace")
        now, rid = self.now(), self._id()
        r = {"id": rid, "workspace_id": workspace_id, "project_id": project_id, "name": name, "is_mock": is_mock, "served_model": None, "tp": None, "pp": None,
             "max_model_len": None, "capabilities": {}, "binary_identity": {}, "last_heartbeat_at": None, "registered_at": None, "disabled_at": None,
             "created_by": user_id, "created_at": now}
        self._tx([self._put({"PK": f"RT#{rid}", "SK": "#", "_sv": 0, **r}), self._put({"PK": f"PRJ#{project_id}", "SK": f"RT#{_ts(now)}#{rid}", "ref": rid})])
        return copy.deepcopy(r)

    def get_runtime(self, runtime_id):
        return self._get(f"RT#{runtime_id}")

    def list_runtimes(self, project_id):
        rows = [_row(i) for i in self._refs(self._query(f"PRJ#{project_id}", begins="RT#"), "RT#")]
        return [r for r in rows if not r["disabled_at"]]

    def add_credential(self, runtime, prefix, secret_hash, expires_at, user_id, rotated_from=None):
        now, cid = self.now(), self._id()
        c = {"id": cid, "workspace_id": runtime["workspace_id"], "project_id": runtime["project_id"], "runtime_id": runtime["id"], "prefix": prefix,
             "secret_hash": secret_hash, "scopes": ["runtime:connect"], "created_by": user_id, "created_at": now, "expires_at": expires_at,
             "revoked_at": None, "rotated_from": rotated_from, "last_used_at": None}
        try:
            self._tx([self._put({"PK": f"CREDPFX#{prefix}", "SK": "#", "ref": cid}, "attribute_not_exists(PK)"),
                      self._put({"PK": f"CRED#{cid}", "SK": "#", **c}),
                      self._put({"PK": f"RT#{runtime['id']}", "SK": f"CRED#{cid}", "ref": cid})])
        except _Conflict:
            raise ValueError("prefix_collision") from None
        return copy.deepcopy(c)

    def get_credential_by_prefix(self, prefix):
        p = self._raw(f"CREDPFX#{prefix}")
        return self._get(f"CRED#{p['ref']['S']}") if p else None

    def _creds(self, runtime_id: str) -> list[dict]:
        return self._refs(self._query(f"RT#{runtime_id}", begins="CRED#"), "CRED#")

    def revoke_credentials(self, runtime_id):
        n, now = 0, self.now()
        for it in self._creds(runtime_id):
            if self._update_item(it["PK"]["S"], "#", "SET #revoked_at = :t", {":t": now}, _isnull("revoked_at")):
                n += 1
        return n

    def revoke_credential(self, credential_id):
        self._update_item(f"CRED#{credential_id}", "#", "SET #revoked_at = :t", {":t": self.now()}, "attribute_exists(PK) AND " + _isnull("revoked_at"))

    def list_credentials(self, runtime_id):
        out = [_row(i) for i in self._creds(runtime_id)]
        return sorted(({k: v for k, v in r.items() if k != "secret_hash"} for r in out), key=lambda r: r["created_at"], reverse=True)

    def touch_credential(self, credential_id):
        self._update_item(f"CRED#{credential_id}", "#", "SET #last_used_at = :t", {":t": self.now()}, "attribute_exists(PK)")

    def disable_runtime(self, runtime_id):
        self._update_item(f"RT#{runtime_id}", "#", "SET #disabled_at = :t", {":t": self.now()}, "attribute_exists(PK)")

    def heartbeat(self, runtime_id):
        now = self.now()
        ok = self._update_item(f"RT#{runtime_id}", "#", "SET #last_heartbeat_at = :t", {":t": now}, "attribute_exists(PK) AND " + _isnull("disabled_at"))
        return now if ok else None

    def register_runtime(self, runtime_id, info):
        now = self.now()
        ok = self._update_item(
            f"RT#{runtime_id}", "#",
            "SET #served_model = :m, #tp = :tp, #pp = :pp, #max_model_len = :ml, #capabilities = :caps, #binary_identity = :bi, #registered_at = :t, #last_heartbeat_at = :t",
            {":m": info["served_model"], ":tp": info["tp"], ":pp": info["pp"], ":ml": info["max_model_len"], ":caps": {"gpus": info["gpus"], **info["capabilities"]},
             ":bi": info["sdk"], ":t": now}, "attribute_exists(PK)")
        if not ok:
            raise KeyError("runtime not found")
        return self._start_run(runtime_id, info)

    # ------------------------------------------------------------------ runs
    def _start_run(self, runtime_id, meta):
        rt = self.get_runtime(runtime_id)
        now = self.now()
        ops: list[dict] = []
        for it in self._refs(self._query(f"RT#{runtime_id}", begins="RUN#"), "RUN#"):
            r = _row(it)
            if r["status"] == "active":
                ops.append(self._upd(it["PK"]["S"], "#", "SET #status = :ab, #ended_at = :t", {":ab": "aborted", ":t": now, ":act": "active"}, "#status = :act"))
                ops.append(self._del("ACTIVE_RUNS", r["id"]))
        rid = self._id()
        run = {"id": rid, "workspace_id": rt["workspace_id"], "project_id": rt["project_id"], "runtime_id": runtime_id, "kind": "session", "is_mock": rt["is_mock"],
               "model": meta.get("served_model"), "tp": meta.get("tp"), "pp": meta.get("pp"), "max_model_len": meta.get("max_model_len"), "status": "active",
               "started_at": now, "ended_at": None, "summary": {}}
        sk = f"RUN#{_ts(now)}#{rid}"
        ops += [self._put({"PK": f"RUN#{rid}", "SK": "#", **run}), self._put({"PK": f"PRJ#{rt['project_id']}", "SK": sk, "ref": rid}),
                self._put({"PK": f"RT#{runtime_id}", "SK": sk, "ref": rid}), self._put({"PK": "ACTIVE_RUNS", "SK": rid, "ref": rid, "runtime_id": runtime_id})]
        for i in range(0, len(ops), 90):
            self._tx(ops[i:i + 90])
        return rid

    def reap_runs(self, stale_seconds=90):
        cutoff, now, n = self.now() - timedelta(seconds=stale_seconds), self.now(), 0
        for it in self._refs(self._query("ACTIVE_RUNS"), "RUN#"):
            r = _row(it)
            if r["status"] != "active":
                continue
            rt = self.get_runtime(r["runtime_id"])
            hb = rt["last_heartbeat_at"] if rt else None
            if hb is None or hb < cutoff:
                try:
                    self._tx([self._upd(it["PK"]["S"], "#", "SET #status = :ab, #ended_at = :t", {":ab": "aborted", ":t": hb or now, ":act": "active"}, "#status = :act"),
                              self._del("ACTIVE_RUNS", r["id"])])
                    n += 1
                except _Conflict:
                    pass
        return n

    def list_runs(self, project_id, limit=50):
        self.reap_runs()
        ptrs = self._query(f"PRJ#{project_id}", begins="RUN#", forward=False, limit=limit)
        return [_row(i) for i in self._refs(ptrs, "RUN#")]

    def get_run(self, run_id):
        return self._get(f"RUN#{run_id}")

    # ------------------------------------------------------------------ chat
    def create_conversation(self, workspace_id, project_id, user_id, title, runtime_id):
        now, cid = self.now(), self._id()
        c = {"id": cid, "workspace_id": workspace_id, "project_id": project_id, "runtime_id": runtime_id, "title": title or "New conversation",
             "created_by": user_id, "created_at": now, "updated_at": now}
        self._tx([self._put({"PK": f"CONV#{cid}", "SK": "#", "_msg_seq": 0, **c}), self._put({"PK": f"PRJ#{project_id}", "SK": f"CONV#{_ts(now)}#{cid}", "ref": cid})])
        return copy.deepcopy(c)

    def get_conversation(self, conversation_id):
        return self._get(f"CONV#{conversation_id}")

    def list_conversations(self, project_id, limit=50):
        ptrs = self._query(f"PRJ#{project_id}", begins="CONV#", forward=False, limit=limit)
        return [_row(i) for i in self._refs(ptrs, "CONV#")]

    def list_messages(self, conversation_id, limit=200):
        return [_row(i) for i in reversed(self._query(f"CONV#{conversation_id}", begins="MSG#", forward=False, limit=limit))]

    def _msg_ops(self, conv_raw: dict, role: str, content: str, job_id: str | None, meta: dict | None, now: datetime) -> tuple[list[dict], str]:
        """Ops that append one message to a conversation and move its 'recently updated' pointer. Guarded by the conversation's _msg_seq."""
        c = _row(conv_raw)
        seq = int(_DE.deserialize(conv_raw["_msg_seq"]))
        mid = self._id()
        msg = {"id": mid, "ordinal": seq + 1, "workspace_id": c["workspace_id"], "conversation_id": c["id"], "role": role, "content": content, "job_id": job_id,
               "meta": meta or {}, "created_at": now}
        old_sk = f"CONV#{_ts(c['updated_at'])}#{c['id']}"
        new_sk = f"CONV#{_ts(now)}#{c['id']}"
        ops = [self._put({"PK": f"CONV#{c['id']}", "SK": f"MSG#{seq + 1:012d}", **msg}, "attribute_not_exists(PK)"),
               self._upd(f"CONV#{c['id']}", "#", "SET #_msg_seq = :n, #updated_at = :t", {":n": seq + 1, ":t": now, ":old": seq}, "#_msg_seq = :old")]
        if old_sk != new_sk:
            ops += [self._del(f"PRJ#{c['project_id']}", old_sk), self._put({"PK": f"PRJ#{c['project_id']}", "SK": new_sk, "ref": c["id"]})]
        return ops, mid

    # ------------------------------------------------------------------ jobs
    def _job_ptr_dels(self, j: Row) -> list[dict]:
        return [self._del(f"RTQ#{j['runtime_id']}", f"{_ts(j['created_at'])}#{j['id']}"), self._del(f"UACT#{j['workspace_id']}#{j['requested_by']}", j["id"])]

    def _job_write(self, raw: dict, sets: dict, extra: list[dict] | None = None, terminal: bool = False) -> None:
        """Atomically update a job iff its version is unchanged (optimistic concurrency), plus any extra ops in the same transaction."""
        j = _row(raw)
        ver = int(_DE.deserialize(raw["_ver"]))
        names = list(sets)
        expr = "SET " + ", ".join(f"#{k} = :{k}" for k in names) + ", #_ver = :nv"
        vals = {f":{k}": v for k, v in sets.items()}
        vals.update({":nv": ver + 1, ":v": ver})
        ops = [self._upd(f"JOB#{j['id']}", "#", expr, vals, "#_ver = :v")]
        if terminal:
            ops += self._job_ptr_dels(j)
        self._tx(ops + (extra or []))

    def _load_job(self, job_id: str) -> dict | None:
        return self._raw(f"JOB#{job_id}")

    def _active_in_queue(self, runtime_id: str) -> list[Row]:
        ptrs = self._query(f"RTQ#{runtime_id}")
        rows = [_row(i) for i in self._refs(ptrs, "JOB#")]
        return [r for r in rows if r["status"] in ACTIVE]

    def submit_job(self, workspace_id, conversation_id, runtime_id, user_id, idem, content, params, char_budget):
        for _ in range(self._retries):
            conv_raw = self._raw(f"CONV#{conversation_id}")
            conv = _row(conv_raw)
            if not conv or conv["workspace_id"] != workspace_id:
                return {"error": "not_found"}
            rt_raw = self._raw(f"RT#{runtime_id}")
            rt = _row(rt_raw)
            if not rt or rt["workspace_id"] != workspace_id or rt["project_id"] != conv["project_id"] or rt["disabled_at"]:
                return {"error": "runtime_not_found"}
            existing = self._get(f"IDEM#{workspace_id}#{idem}")
            if existing:
                if existing["conversation_id"] != conversation_id:
                    return {"error": "idempotency_conflict"}
                j = self._get(f"JOB#{existing['job_id']}")
                return {"ok": True, "job_id": existing["job_id"], "status": j["status"] if j else "pending", "idempotent": True}
            if len(content) > char_budget:
                return {"error": "prompt_too_long", "char_budget": char_budget}
            if len(self._active_in_queue(runtime_id)) >= CAPS["active_jobs_per_runtime"]:
                return {"error": "too_many_jobs"}
            uact = self._query(f"UACT#{workspace_id}#{user_id}")
            u_ptrs = [p for p in uact if p["SK"]["S"] != "LOCK"]
            if sum(1 for j in (_row(i) for i in self._refs(u_ptrs, "JOB#")) if j["status"] in ACTIVE) >= CAPS["active_jobs_per_user"]:
                return {"error": "too_many_user_jobs"}
            lock = next((p for p in uact if p["SK"]["S"] == "LOCK"), None)
            lock_v = int(_DE.deserialize(lock["_v"])) if lock else 0
            sv = int(_DE.deserialize(rt_raw["_sv"])) if "_sv" in rt_raw else 0

            now = self.now()
            # history = newest-first until the character budget is exceeded (the NEW message always counts first), then oldest-first
            hist, running = [{"role": "user", "content": content}], len(content)
            for m in self._query(f"CONV#{conversation_id}", begins="MSG#", forward=False, limit=200):
                mr = _row(m)
                running += len(mr["content"])
                if running > char_budget:
                    break
                hist.append({"role": mr["role"], "content": mr["content"]})
            hist.reverse()
            job_id = self._id()
            job = {"id": job_id, "workspace_id": workspace_id, "project_id": conv["project_id"], "conversation_id": conversation_id, "runtime_id": runtime_id,
                   "requested_by": user_id, "idempotency_key": idem, "status": "pending",
                   "request": {"messages": hist, "max_tokens": int(params["max_tokens"]), "temperature": float(params["temperature"]), "model": rt["served_model"]},
                   "attempt": 0, "max_attempts": 2, "lease_owner": None, "lease_expires_at": None, "last_event_seq": 0, "event_count": 0, "text_chars": 0,
                   "created_at": now, "deadline_at": now + timedelta(minutes=10), "claimed_at": None, "first_token_at": None, "finished_at": None,
                   "cancel_requested_at": None, "error_code": None, "error_message": None, "result": None, "assistant_message_id": None}
            msg_ops, mid = self._msg_ops(conv_raw, "user", content, None, None, now)
            # the conversation row also records the runtime used last
            msg_ops[1]["Update"]["UpdateExpression"] += ", #runtime_id = :rid"
            msg_ops[1]["Update"]["ExpressionAttributeNames"]["#runtime_id"] = "runtime_id"
            msg_ops[1]["Update"]["ExpressionAttributeValues"][":rid"] = _SER.serialize(runtime_id)
            ops = [
                # two optimistic locks serialise concurrent submits so the per-runtime / per-user caps cannot be exceeded by a race
                self._upd(f"RT#{runtime_id}", "#", "SET #_sv = :nsv", {":nsv": sv + 1, ":sv": sv}, "attribute_not_exists(#_sv) OR #_sv = :sv"),
                self._upd(f"UACT#{workspace_id}#{user_id}", "LOCK", "SET #_v = :nv", {":nv": lock_v + 1, ":v": lock_v}, "attribute_not_exists(#_v) OR #_v = :v"),
                self._put({"PK": f"IDEM#{workspace_id}#{idem}", "SK": "#", "workspace_id": workspace_id, "job_id": job_id, "conversation_id": conversation_id}, "attribute_not_exists(PK)"),
                self._put({"PK": f"JOB#{job_id}", "SK": "#", "_ver": 0, **job}),
                self._put({"PK": f"RTQ#{runtime_id}", "SK": f"{_ts(now)}#{job_id}", "ref": job_id}),
                self._put({"PK": f"UACT#{workspace_id}#{user_id}", "SK": job_id, "ref": job_id}),
                *msg_ops,
            ]
            try:
                self._tx(ops)
                return {"ok": True, "job_id": job_id, "status": "pending", "user_message_id": mid}
            except _Conflict as c:
                if len(c.reasons) > 2 and c.reasons[2] == "ConditionalCheckFailed":      # idempotency key written concurrently
                    continue
                time.sleep(0.003 * random.random())
                continue                                                                  # lock/sequence moved: re-evaluate everything
        raise StoreUnavailable("database contention: submit_job")

    # ---- lifecycle helpers
    def _expiry_updates(self, j: Row, now: datetime) -> tuple[dict, bool] | None:
        if j["status"] == "pending" and j["deadline_at"] < now:
            return {"status": "expired", "finished_at": now, "error_code": "deadline", "error_message": "job was not completed before its deadline"}, True
        if j["status"] == "cancel_requested" and j["lease_expires_at"] and j["lease_expires_at"] < now:
            return {"status": "cancelled", "finished_at": now}, True
        if j["status"] in ("claimed", "running") and j["lease_expires_at"] and j["lease_expires_at"] < now and (j["attempt"] >= j["max_attempts"] or j["deadline_at"] < now):
            return {"status": "failed", "finished_at": now, "error_code": "lease_expired", "error_message": "runtime stopped responding and no attempts remain"}, True
        return None

    def _expire_one(self, raw: dict) -> None:
        j = _row(raw)
        u = self._expiry_updates(j, self.now())
        if u:
            try:
                self._job_write(raw, u[0], terminal=True)
            except _Conflict:
                pass                                                     # someone else moved it; they own the transition

    def expire_jobs(self, runtime_id=None):
        if runtime_id is None:
            return                                                        # global sweep intentionally not supported (see module docstring)
        ptrs = self._query(f"RTQ#{runtime_id}")
        keys = [(f"JOB#{p['ref']['S']}", "#") for p in ptrs]
        got = self._batch_get(keys)
        for p, k in zip(ptrs, keys):
            raw = got.get(k)
            if raw is not None:
                self._expire_one(raw)
                raw = self._load_job(k[0][4:])
            if raw is None or _row(raw)["status"] in TERMINAL:            # dangling / stale pointer: drop it (correctness never depends on it)
                self._call("delete_item", TableName=self.table, Key=_key(p["PK"]["S"], p["SK"]["S"]))

    def claim_job(self, runtime_id, owner, lease_seconds):
        rt = self.get_runtime(runtime_id)
        if not rt or rt["disabled_at"]:
            return None
        self.expire_jobs(runtime_id)
        lease = min(max(lease_seconds, 5), 120)
        for _ in range(self._retries):
            now = self.now()
            cands = []
            for raw in self._refs(self._query(f"RTQ#{runtime_id}", limit=25), "JOB#"):
                j = _row(raw)
                if j["deadline_at"] > now and (j["status"] == "pending" or (j["status"] in ("claimed", "running") and j["lease_expires_at"] and
                                                                              j["lease_expires_at"] < now and j["attempt"] < j["max_attempts"])):
                    cands.append((raw, j))
            if not cands:
                return None
            raw, j = min(cands, key=lambda t: t[1]["created_at"])
            upd = {"status": "claimed", "lease_owner": owner, "lease_expires_at": now + timedelta(seconds=lease), "attempt": j["attempt"] + 1,
                   "claimed_at": now, "first_token_at": None}
            try:
                self._job_write(raw, upd)
            except _Conflict:
                continue                                                  # lost the race for this job: look again (maybe another is eligible)
            return {**j, **upd}
        return None

    def _job_text(self, job_id: str) -> str:
        evs = [_row(i) for i in self._query(f"JOB#{job_id}", begins="EVT#")]
        last_reset = max((e["event_seq"] for e in evs if e["kind"] == "reset"), default=0)
        return "".join(e["text"] or "" for e in evs if e["kind"] == "delta" and e["event_seq"] > last_reset)

    @staticmethod
    def _lease_ok(j: Row, owner: str, now: datetime) -> bool:
        return j["lease_owner"] == owner and j["lease_expires_at"] is not None and j["lease_expires_at"] >= now

    def append_events(self, runtime_id, job_id, owner, events, lease_seconds):
        lease = min(max(lease_seconds, 5), 120)
        for _ in range(self._retries):
            raw = self._load_job(job_id)
            j = _row(raw)
            if not j or j["runtime_id"] != runtime_id:
                return {"error": "not_found"}
            if j["status"] in TERMINAL:
                return {"error": "terminal", "status": j["status"]}
            now = self.now()
            if not self._lease_ok(j, owner, now):
                return {"error": "lease_lost"}
            last, accepted, dups, first_new, has_delta = j["last_event_seq"], 0, 0, None, False
            event_count, text_chars = j["event_count"], j["text_chars"]
            new_events: list[dict] = []
            outcome: dict | None = None
            failed = False
            for e in sorted(events, key=lambda e: e["event_seq"]):
                seq = e["event_seq"]
                if seq <= last:
                    dups += 1
                    continue
                if seq > last + 1:
                    outcome = {"error": "gap", "expected_seq": last + 1, "last_event_seq": last, "accepted": accepted}
                    break
                n = len(e.get("text") or "")
                if event_count + 1 > CAPS["job_events"] or text_chars + n > CAPS["job_text_chars"]:
                    failed = True
                    break
                kind = e.get("kind", "delta")
                new_events.append({"PK": f"JOB#{job_id}", "SK": f"EVT#{seq:010d}", "job_id": job_id, "event_seq": seq, "workspace_id": j["workspace_id"], "kind": kind,
                                   "text": e.get("text"), "token_count": e.get("token_count"), "chunk_count": e.get("chunk_count"), "emitted_at": e.get("emitted_at"), "received_at": now,
                                   "_t": "event", "ttl": int(now.timestamp()) + self._ev_ttl_s})
                event_count += 1
                if kind == "reset":
                    text_chars = 0
                if kind == "delta":
                    has_delta, text_chars = True, text_chars + n
                first_new = first_new or seq
                last, accepted = seq, accepted + 1
            was_cancel = j["status"] == "cancel_requested"
            extra = [self._put(ev, "attribute_not_exists(PK)") for ev in new_events]
            try:
                if failed:
                    self._job_write(raw, {"last_event_seq": last, "event_count": event_count, "status": "failed", "finished_at": now, "error_code": "cap_exceeded",
                                          "error_message": "job output exceeded the event or text cap"}, extra, terminal=True)
                    return {"error": "cap_exceeded"}
                sets = {"last_event_seq": last, "event_count": event_count, "text_chars": text_chars, "lease_expires_at": now + timedelta(seconds=lease)}
                if outcome is None:                                       # a gap keeps the status; a clean batch moves claimed -> running
                    sets["status"] = j["status"] if was_cancel else "running"
                    if has_delta and not j["first_token_at"]:
                        sets["first_token_at"] = now
                self._job_write(raw, sets, extra)
            except _Conflict:
                continue                                                  # job moved (another append/claim): re-read and re-evaluate
            if outcome is not None:
                return outcome
            return {"ok": True, "last_event_seq": last, "accepted": accepted, "duplicates": dups, "first_new_seq": first_new, "cancel_requested": was_cancel}
        raise StoreUnavailable("database contention: append_events")

    def complete_job(self, runtime_id, job_id, owner, final_seq, text_sha256, result):
        for _ in range(self._retries):
            raw = self._load_job(job_id)
            j = _row(raw)
            if not j or j["runtime_id"] != runtime_id:
                return {"error": "not_found"}
            if j["status"] == "succeeded":
                if (j["result"] or {}).get("text_sha256") == text_sha256:
                    return {"ok": True, "idempotent": True, "assistant_message_id": j["assistant_message_id"]}
                return {"error": "conflict"}
            if j["status"] in TERMINAL:
                return {"error": "terminal", "status": j["status"]}
            now = self.now()
            if not self._lease_ok(j, owner, now):
                return {"error": "lease_lost"}
            if final_seq != j["last_event_seq"]:
                return {"error": "seq_mismatch", "expected_seq": j["last_event_seq"]}
            text = self._job_text(job_id)
            if hashlib.sha256(text.encode()).hexdigest() != text_sha256:
                return {"error": "hash_mismatch"}
            conv_raw = self._raw(f"CONV#{j['conversation_id']}")
            msg_ops, mid = self._msg_ops(conv_raw, "assistant", text, job_id, {"measured": result or {}, "runtime_id": runtime_id}, now)
            try:
                self._job_write(raw, {"status": "succeeded", "finished_at": now, "assistant_message_id": mid, "result": {**(result or {}), "text_sha256": text_sha256},
                                      "lease_expires_at": None}, msg_ops, terminal=True)
                return {"ok": True, "assistant_message_id": mid}
            except _Conflict:
                continue
        raise StoreUnavailable("database contention: complete_job")

    def fail_job(self, runtime_id, job_id, owner, code, message, retryable):
        for _ in range(self._retries):
            raw = self._load_job(job_id)
            j = _row(raw)
            if not j or j["runtime_id"] != runtime_id:
                return {"error": "not_found"}
            if j["status"] in TERMINAL:
                return {"ok": True, "idempotent": True, "status": j["status"]}
            now = self.now()
            if not self._lease_ok(j, owner, now):
                return {"error": "lease_lost"}
            try:
                if retryable and j["attempt"] < j["max_attempts"] and j["status"] != "cancel_requested":
                    self._job_write(raw, {"status": "pending", "lease_owner": None, "lease_expires_at": None, "error_code": code[:60], "error_message": message[:500]})
                    return {"ok": True, "status": "pending", "requeued": True}
                self._job_write(raw, {"status": "cancelled" if j["status"] == "cancel_requested" else "failed", "finished_at": now, "error_code": code[:60],
                                      "error_message": message[:500], "lease_expires_at": None}, terminal=True)
                return {"ok": True, "status": "failed"}
            except _Conflict:
                continue
        raise StoreUnavailable("database contention: fail_job")

    def ack_cancel(self, runtime_id, job_id, owner):
        for _ in range(self._retries):
            raw = self._load_job(job_id)
            j = _row(raw)
            if not j or j["runtime_id"] != runtime_id:
                return {"error": "not_found"}
            if j["status"] in TERMINAL:
                return {"ok": True, "idempotent": True, "status": j["status"]}
            now = self.now()
            if not self._lease_ok(j, owner, now):
                return {"error": "lease_lost"}
            if j["status"] != "cancel_requested":
                return {"error": "not_cancel_requested", "status": j["status"]}
            text = self._job_text(job_id)
            extra: list[dict] = []
            if text:
                extra, _mid = self._msg_ops(self._raw(f"CONV#{j['conversation_id']}"), "assistant", text, job_id, {"cancelled": True}, now)
            try:
                self._job_write(raw, {"status": "cancelled", "finished_at": now, "lease_expires_at": None}, extra, terminal=True)
                return {"ok": True, "status": "cancelled"}
            except _Conflict:
                continue
        raise StoreUnavailable("database contention: ack_cancel")

    def request_cancel(self, workspace_id, job_id):
        for _ in range(self._retries):
            raw = self._load_job(job_id)
            j = _row(raw)
            if not j or j["workspace_id"] != workspace_id:
                return {"error": "not_found"}
            now = self.now()
            try:
                if j["status"] == "pending":
                    self._job_write(raw, {"status": "cancelled", "finished_at": now, "cancel_requested_at": now}, terminal=True)
                    return {"ok": True, "status": "cancelled"}
                if j["status"] in ("claimed", "running"):
                    self._job_write(raw, {"status": "cancel_requested", "cancel_requested_at": now})
                    return {"ok": True, "status": "cancel_requested"}
            except _Conflict:
                continue
            return {"ok": True, "status": j["status"], "idempotent": True}
        raise StoreUnavailable("database contention: request_cancel")

    def get_job(self, job_id):
        raw = self._load_job(job_id)
        if not raw:
            return None
        self._expire_one(raw)
        return self._get(f"JOB#{job_id}")

    def list_events(self, job_id, after_seq, limit):
        return [_row(i) for i in self._query(f"JOB#{job_id}", gt=f"EVT#{int(after_seq):010d}", limit=limit)]

    # ------------------------------------------------------------------ telemetry
    def ingest_telemetry(self, runtime_id, batch_id, run_id, gpu, vllm):
        rt = self.get_runtime(runtime_id)
        if not rt or rt["disabled_at"]:
            return {"error": "not_found"}
        if len(gpu) > CAPS["gpu_rows"] or len(vllm) > CAPS["vllm_rows"]:
            return {"error": "batch_too_large"}
        if run_id:
            run = self.get_run(run_id)
            if not run or run["runtime_id"] != runtime_id:
                return {"error": "run_not_found"}
        if self._raw(f"BATCH#{runtime_id}", batch_id):
            return {"ok": True, "duplicate": True}
        now = self.now()
        ttl = int(now.timestamp()) + self._tm_ttl_s
        base = {"workspace_id": rt["workspace_id"], "project_id": rt["project_id"], "runtime_id": runtime_id, "run_id": run_id, "is_mock": rt["is_mock"]}
        g_items: dict[str, dict] = {}
        for g in gpu:
            ts = _aware(g["captured_at"])
            g_items[f"{_ts(ts)}#{runtime_id}#{int(g['gpu_index']):03d}"] = {**g, **base, "captured_at": ts, "_t": "gpu", "ttl": ttl}
        v_items: dict[str, dict] = {}
        for v in vllm:
            ts = _aware(v["captured_at"])
            v_items[f"{_ts(ts)}#{runtime_id}"] = {**v, **base, "captured_at": ts, "_t": "vllm", "ttl": ttl}
        # "ON CONFLICT DO NOTHING": rows that already exist (e.g. re-sent under a NEW batch id) are skipped and not counted. One consistent
        # BatchGetItem pre-check (about 1 call per 100 rows). Two concurrent batches racing on identical keys may both write the same row (idempotent).
        have = self._batch_get([(f"GPU#{rt['project_id']}", sk) for sk in g_items] + [(f"VLLM#{rt['project_id']}", sk) for sk in v_items])
        g_items = {sk: it for sk, it in g_items.items() if (f"GPU#{rt['project_id']}", sk) not in have}
        v_items = {sk: it for sk, it in v_items.items() if (f"VLLM#{rt['project_id']}", sk) not in have}
        reqs = [{"PutRequest": {"Item": _marshal({"PK": f"GPU#{rt['project_id']}", "SK": sk, **it})}} for sk, it in g_items.items()]
        reqs += [{"PutRequest": {"Item": _marshal({"PK": f"VLLM#{rt['project_id']}", "SK": sk, **it})}} for sk, it in v_items.items()]
        for i in range(0, len(reqs), 25):
            todo = {self.table: reqs[i:i + 25]}
            for _ in range(6):
                r = self._call("batch_write_item", RequestItems=todo)
                todo = r.get("UnprocessedItems") or {}
                if not todo:
                    break
            else:
                raise StoreUnavailable("database throttled: telemetry batch")
        try:                                                                  # marker LAST: a crash before this point re-writes identical rows on retry
            self._tx([self._put({"PK": f"BATCH#{runtime_id}", "SK": batch_id, "_t": "batch", "received_at": now, "ttl": int(now.timestamp()) + 2 * 86400},
                                "attribute_not_exists(PK)")])
        except _Conflict:
            return {"ok": True, "duplicate": True}
        return {"ok": True, "gpu_rows": len(g_items), "vllm_rows": len(v_items)}

    def _window(self, prefix: str, project_id, runtime_id, since: datetime, until: datetime, limit: int):
        lo, hi = _ts(_aware(since)), _ts(_aware(until)) + "~"                 # '~' sorts after every runtime/gpu suffix: until is inclusive
        filt, fvals = ("#runtime_id = :rt", {":rt": runtime_id}) if runtime_id else (None, None)
        return [_row(i) for i in self._query(f"{prefix}#{project_id}", between=(lo, hi), limit=limit, filt=filt, fvals=fvals)]

    def query_gpu(self, project_id, runtime_id, since, until, limit):
        return self._window("GPU", project_id, runtime_id, since, until, limit)

    def query_vllm(self, project_id, runtime_id, since, until, limit):
        return self._window("VLLM", project_id, runtime_id, since, until, limit)

    # ------------------------------------------------------------------ housekeeping
    def ready(self):
        self._call("get_item", TableName=self.table, Key=_key("READY#probe", "READY"), ConsistentRead=False)     # a read of an absent key: needs only dynamodb:GetItem

    def prune(self, event_hours=48, gpu_days=14, vllm_days=14):
        """Bounded scan that deletes aged rows. DynamoDB TTL does the same lazily (~48 h); this gives exact-age deletion when called.
        Events are removed only for TERMINAL jobs. The scan is capped at ``scan_page_cap`` pages per call, so a very large backlog drains over several calls."""
        now = self.now()
        cut = {"event": now - timedelta(hours=event_hours), "gpu": now - timedelta(days=gpu_days), "vllm": now - timedelta(days=vllm_days), "batch": now - timedelta(days=2)}
        counts = {"event": 0, "gpu": 0, "vllm": 0, "batch": 0}
        job_terminal: dict[str, bool] = {}
        start, pages, dels = None, 0, []
        while pages < self._scan_cap:
            kw: dict = dict(TableName=self.table, FilterExpression="attribute_exists(#t)", ExpressionAttributeNames={"#t": "_t"}, ConsistentRead=True)
            if start:
                kw["ExclusiveStartKey"] = start
            r = self._call("scan", **kw)
            pages += 1
            for it in r.get("Items", []):
                t = it["_t"]["S"]
                when = _dt(it["received_at"]["S"] if t in ("event", "batch") else it["captured_at"]["S"])
                if when >= cut[t]:
                    continue
                if t == "event":
                    jid = it["job_id"]["S"]
                    if jid not in job_terminal:
                        jr = self._get(f"JOB#{jid}")
                        job_terminal[jid] = (jr is None) or jr["status"] in TERMINAL
                    if not job_terminal[jid]:
                        continue
                dels.append((it["PK"]["S"], it["SK"]["S"], t))
            start = r.get("LastEvaluatedKey")
            if not start:
                break
        for i in range(0, len(dels), 25):
            chunk = dels[i:i + 25]
            todo = {self.table: [{"DeleteRequest": {"Key": _key(pk, sk)}} for pk, sk, _t in chunk]}
            for _ in range(6):
                res = self._call("batch_write_item", RequestItems=todo)
                todo = res.get("UnprocessedItems") or {}
                if not todo:
                    break
            for _pk, _sk, t in chunk:
                counts[t] += 1
        return {"events": counts["event"], "gpu_samples": counts["gpu"], "vllm_samples": counts["vllm"], "batches": counts["batch"]}

    def audit(self, workspace_id, actor, action, target_type=None, target_id=None, meta=None):
        now = self.now()
        self._call("put_item", TableName=self.table, Item=_marshal({"PK": f"AUDIT#{workspace_id or '-'}", "SK": f"{_ts(now)}#{self._id()}", "workspace_id": workspace_id,
                                                                    "actor": actor, "action": action, "target_type": target_type, "target_id": target_id,
                                                                    "meta": meta or {}, "at": now}))


CAPS_OWNER = 5       # owned workspaces per user (matches enigma_create_workspace)
