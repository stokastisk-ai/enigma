"""Enigma independent diagnostic ingestion (AWS Lambda behind a Function URL). Standard library + boto3 (bundled in the Lambda Python runtime).

Purpose: Kaggle sessions have been terminated before any local diagnostic archive could be downloaded. A notebook persists small, structured
before/after markers HERE, independent of Railway and of the Enigma database, so the last acknowledged marker survives a VM loss.

Security model (the Function URL has AuthType NONE; authentication is done here):
  * Every request carries ``Authorization: Bearer edx_<8-char id>_<43-char secret>``: a narrow, revocable, experiment-specific diagnostic key. Only
    sha256(secret) is stored (item ``DIAGKEY#<id>``); comparison is constant-time; unknown, revoked, expired and wrong keys get the same 401.
  * Each key has an atomic event quota (``used + n <= max_events``, a conditional update) and an expiry. Reserved concurrency (set on the function)
    and a short timeout bound the cost of abuse.
  * Bodies are limited to 32 KiB and 25 events. Every event is validated against a fixed whitelist of typed fields; unknown fields are refused, never
    stored. Strings that look like credentials (runtime tokens, AWS keys, bearer tokens, JWTs, connection strings) are refused.
Persistence: one item per event (``ATT#<attempt>`` / ``EVT#<seq>``), written with ``attribute_not_exists`` so a resubmitted event is acknowledged
as a duplicate and never overwritten. The response is sent only after every write succeeded. Items carry a TTL. One compact JSON line per request
goes to CloudWatch Logs (counts and marker names only)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any

MAX_BODY = 32 * 1024
MAX_EVENTS = 25
TTL_DAYS = int(os.environ.get("DIAG_TTL_DAYS", "30"))
TABLE = os.environ.get("DIAG_TABLE", "enigma-diag")

_KEY_RE = re.compile(r"^edx_([a-z0-9]{8})_([A-Za-z0-9_-]{43})$")
_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_SECRETS = (re.compile(r"enig_rt_[a-z0-9]{8}_"), re.compile(r"edx_[a-z0-9]{8}_[A-Za-z0-9_-]{20,}"), re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
            re.compile(r"(?i)bearer\s+\S{8,}"), re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\."), re.compile(r"(?i)postgres(ql)?://"),
            re.compile(r"(?i)(aws_secret_access_key|secret_access_key|password)\s*[=:]"))

_STR = lambda n: ("str", n)            # noqa: E731
_NUM, _INT, _BOOL = ("num", None), ("int", None), ("bool", None)
FIELDS: dict[str, tuple[str, int | None]] = {
    "attempt": _STR(80), "nb": _STR(80), "seq": _INT, "marker": _STR(80), "stage": _STR(80), "phase": _STR(16), "utc": _STR(40), "t": _NUM,
    "http": _INT, "latency_s": _NUM, "target": _STR(80), "exc": _STR(80), "exit_code": _INT, "signal": _STR(24), "last_ok": _STR(80),
    "verdict": _STR(16), "note": _STR(300), "oom_kill": _INT, "mem_available_mib": _NUM, "cgroup_current_mib": _NUM, "cgroup_max_mib": _NUM,
    "vm_uptime_s": _NUM, "gpu_count": _INT, "gpu_mem_used_mib": ("numlist", 8), "gpu_util_pct": ("numlist", 8), "wheel_build": _STR(8),
    "wheel_sha256": _STR(64), "artifact": _STR(120), "pid": _INT, "pgid": _INT, "sid": _INT, "experiment": _STR(80),
}
REQUIRED = ("attempt", "nb", "seq", "marker", "utc")


class Reject(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


def _resp(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"}, "body": json.dumps(body)}


def _looks_secret(s: str) -> bool:
    return any(p.search(s) for p in _SECRETS)


def validate_event(ev: Any) -> dict:
    if not isinstance(ev, dict):
        raise Reject(400, "event_not_object")
    unknown = set(ev) - set(FIELDS)
    if unknown:
        raise Reject(400, "unknown_field")
    for k in REQUIRED:
        if k not in ev:
            raise Reject(400, "missing_field")
    out: dict[str, Any] = {}
    for k, v in ev.items():
        kind, n = FIELDS[k]
        if v is None:
            continue
        if kind == "str":
            if not isinstance(v, str) or len(v) > n or _looks_secret(v):
                raise Reject(400, "bad_field")
        elif kind == "int":
            if isinstance(v, bool) or not isinstance(v, int) or not -10**12 < v < 10**12:
                raise Reject(400, "bad_field")
        elif kind == "num":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > 1e15:
                raise Reject(400, "bad_field")
        elif kind == "numlist":
            if not isinstance(v, list) or len(v) > n or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and x == x for x in v):
                raise Reject(400, "bad_field")
        out[k] = v
    if not _ID_RE.fullmatch(out["attempt"]) or not _ID_RE.fullmatch(out["nb"]) or not 0 <= out["seq"] < 10**8:
        raise Reject(400, "bad_identity")
    return out


def _ddb_value(v: Any) -> dict:
    from decimal import Decimal
    if isinstance(v, bool):
        return {"BOOL": v}
    if isinstance(v, int):
        return {"N": str(v)}
    if isinstance(v, float):
        return {"N": str(Decimal(repr(v)))}
    if isinstance(v, list):
        return {"L": [_ddb_value(x) for x in v]}
    return {"S": str(v)}


def authenticate(ddb: Any, header: str, n_events: int, now: float) -> str:
    """Returns the key id after charging ``n_events`` to its quota. Same 401 for every authentication failure."""
    fail = Reject(401, "unauthorized")
    m = _KEY_RE.match((header or "").removeprefix("Bearer ").strip())
    if not m:
        raise fail
    kid, secret = m.groups()
    item = ddb.get_item(TableName=TABLE, Key={"PK": {"S": f"DIAGKEY#{kid}"}, "SK": {"S": "#"}}, ConsistentRead=True).get("Item")
    expected = item["secret_hash"]["S"] if item else "0" * 64
    if not (hmac.compare_digest(hashlib.sha256(secret.encode()).hexdigest(), expected) and item):
        raise fail
    max_events = int(item["max_events"]["N"])
    try:
        # DynamoDB conditions have no arithmetic: compare against a precomputed bound, and pin max_events itself so the bound cannot go stale.
        ddb.update_item(TableName=TABLE, Key={"PK": {"S": f"DIAGKEY#{kid}"}, "SK": {"S": "#"}},
                        UpdateExpression="SET used = used + :n",
                        ConditionExpression="revoked = :f AND expires_at > :now AND max_events = :max AND used <= :limit",
                        ExpressionAttributeValues={":n": {"N": str(n_events)}, ":f": {"BOOL": False}, ":now": {"N": str(int(now))},
                                                   ":max": {"N": str(max_events)}, ":limit": {"N": str(max_events - n_events)}})
    except ddb.exceptions.ConditionalCheckFailedException:
        revoked = item.get("revoked", {}).get("BOOL") or int(item["expires_at"]["N"]) <= now
        raise Reject(401, "unauthorized") if revoked else Reject(429, "quota_exhausted")
    return kid


def handle(event: dict, ddb: Any, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    try:
        if (event.get("requestContext", {}).get("http", {}).get("method") or "POST") != "POST":
            raise Reject(405, "method_not_allowed")
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8", "replace")
        if len(raw.encode()) > MAX_BODY:
            raise Reject(413, "too_large")
        try:
            body = json.loads(raw)
        except ValueError:
            raise Reject(400, "bad_json") from None
        events = body.get("events") if isinstance(body, dict) else None
        if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
            raise Reject(400, "bad_events")
        clean = [validate_event(e) for e in events]                       # validate BEFORE charging the quota
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        kid = authenticate(ddb, headers.get("authorization", ""), len(clean), now)
        stored = dups = 0
        ttl = int(now) + TTL_DAYS * 86400
        for ev in clean:
            item = {"PK": {"S": f"ATT#{ev['attempt']}"}, "SK": {"S": f"EVT#{ev['seq']:08d}"}, "ttl": {"N": str(ttl)},
                    "received_at": {"N": str(int(now))}, "key_id": {"S": kid}}
            item.update({k: _ddb_value(v) for k, v in ev.items()})
            try:
                ddb.put_item(TableName=TABLE, Item=item, ConditionExpression="attribute_not_exists(PK)")
                stored += 1
            except ddb.exceptions.ConditionalCheckFailedException:
                dups += 1
        print(json.dumps({"diag": "ingest", "key": kid, "attempt": clean[0]["attempt"], "stored": stored, "duplicates": dups,
                          "markers": [e["marker"] for e in clean][:25]}))
        return _resp(200, {"stored": stored, "duplicates": dups})
    except Reject as r:
        print(json.dumps({"diag": "reject", "status": r.status, "code": r.code}))
        return _resp(r.status, {"error": r.code})


_DDB = None


def lambda_handler(event: dict, context: Any) -> dict:
    global _DDB
    if _DDB is None:
        import boto3
        _DDB = boto3.client("dynamodb")
    try:
        return handle(event, _DDB)
    except Exception as e:                                                # noqa: BLE001 - never leak internals; failure is NOT an acknowledgement
        print(json.dumps({"diag": "error", "type": type(e).__name__}))
        return _resp(503, {"error": "unavailable"})
