"""Kaggle diagnostic marker ingestion: POST /api/v1/diag/events (replaces the AWS diag-ingest Lambda; same request/response contract).

Purpose: Kaggle sessions have been terminated before any local diagnostic archive could be downloaded. A notebook persists small, structured
before/after markers here, so the last acknowledged marker survives a VM loss. It shares the VM and database with the app (one failure domain,
acknowledged); it is logically separate: own tables (0006), own credential type, own route, no access to tenant data.

Security model (unchanged from the Lambda):
  * ``Authorization: Bearer edx_<8-char id>_<43-char secret>``: a narrow, revocable, experiment-specific key, never a runtime token. Only
    sha256(secret) is stored; comparison is constant-time; unknown, wrong and malformed keys all get the same 401.
  * Atomic per-key event quota and expiry (one conditional UPDATE); revoked/expired -> 401 key_expired_or_revoked, exhausted -> 429.
  * Bodies <= 32 KiB and <= 25 events, validated BEFORE the quota is charged against a fixed whitelist of typed fields; unknown fields are refused;
    strings that look like credentials are refused (never stored).
  * Idempotent: (attempt, seq) is the primary key; a resubmitted event is acknowledged as a duplicate and never overwritten. The response is sent
    only after the insert committed. Retention: diag_prune() (called by the admin prune timer) deletes events older than DIAG_TTL_DAYS.
Prompts, completions and tokens are not accepted fields, so they cannot be stored here by default.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

MAX_BODY = 32 * 1024
MAX_EVENTS = 25
TTL_DAYS = 30

_KEY_RE = re.compile(r"^edx_([a-z0-9]{8})_([A-Za-z0-9_-]{43})$")
_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_SECRETS = (re.compile(r"enig_rt_[a-z0-9]{8}_"), re.compile(r"edx_[a-z0-9]{8}_[A-Za-z0-9_-]{20,}"), re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
            re.compile(r"(?i)bearer\s+\S{8,}"), re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\."), re.compile(r"(?i)postgres(ql)?://"),
            re.compile(r"(?i)(aws_secret_access_key|secret_access_key|password)\s*[=:]"), re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"),
            re.compile(r"\bocid1\.[a-z]+\.oc1\."))

_STR = lambda n: ("str", n)            # noqa: E731
_NUM, _INT = ("num", None), ("int", None)
FIELDS: dict[str, tuple[str, int | None]] = {
    "attempt": _STR(80), "nb": _STR(80), "seq": _INT, "marker": _STR(80), "stage": _STR(80), "phase": _STR(16), "utc": _STR(40), "t": _NUM,
    "http": _INT, "latency_s": _NUM, "target": _STR(80), "exc": _STR(80), "exit_code": _INT, "signal": _STR(24), "last_ok": _STR(80),
    "verdict": _STR(16), "note": _STR(300), "oom_kill": _INT, "mem_available_mib": _NUM, "cgroup_current_mib": _NUM, "cgroup_max_mib": _NUM,
    "vm_uptime_s": _NUM, "gpu_count": _INT, "gpu_mem_used_mib": ("numlist", 8), "gpu_util_pct": ("numlist", 8), "wheel_build": _STR(8),
    "wheel_sha256": _STR(64), "artifact": _STR(120), "pid": _INT, "pgid": _INT, "sid": _INT, "experiment": _STR(80),
    "transport": _STR(24), "offset_s": _NUM, "child_pid": _INT, "child_exit": _INT, "outcome": _STR(40), "oom_state": _STR(16),
}
REQUIRED = ("attempt", "nb", "seq", "marker", "utc")


class Reject(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


def _looks_secret(s: str) -> bool:
    return any(p.search(s) for p in _SECRETS)


def validate_event(ev: Any) -> dict:
    if not isinstance(ev, dict):
        raise Reject(400, "event_not_object")
    if set(ev) - set(FIELDS):
        raise Reject(400, "unknown_field")
    if any(k not in ev for k in REQUIRED):
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
    if any(k not in out for k in REQUIRED):
        raise Reject(400, "missing_field")
    if not _ID_RE.fullmatch(out["attempt"]) or not _ID_RE.fullmatch(out["nb"]) or not 0 <= out["seq"] < 10**8:
        raise Reject(400, "bad_identity")
    return out


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


# ---------------------------------------------------------------- backends
class MemoryDiag:
    """In-process backend for tests and the memory store (not durable)."""

    def __init__(self) -> None:
        self.keys: dict[str, dict] = {}
        self.events: dict[tuple[str, int], dict] = {}
        self._l = threading.Lock()

    def add_key(self, kid: str, secret_hash: str, label: str, max_events: int, expires_at: datetime) -> None:
        self.keys[kid] = {"id": kid, "secret_hash": secret_hash, "label": label, "max_events": max_events, "used": 0, "expires_at": expires_at, "revoked": False}

    def get_key(self, kid: str) -> dict | None:
        return dict(self.keys[kid]) if kid in self.keys else None

    def charge(self, kid: str, n: int, now: datetime) -> str:
        with self._l:
            k = self.keys.get(kid)
            if not k or k["revoked"] or k["expires_at"] <= now:
                return "expired_or_revoked"
            if k["used"] + n > k["max_events"]:
                return "quota"
            k["used"] += n
            return "ok"

    def insert(self, kid: str, events: list[dict], now: datetime) -> int:
        with self._l:
            stored = 0
            for e in events:
                key = (e["attempt"], e["seq"])
                if key not in self.events:
                    self.events[key] = {**e, "key_id": kid, "received_at": now}
                    stored += 1
            return stored

    def prune(self, now: datetime, days: int = TTL_DAYS) -> int:
        with self._l:
            old = [k for k, v in self.events.items() if v["received_at"] < now - timedelta(days=days)]
            for k in old:
                del self.events[k]
            return len(old)


class PostgresDiag:
    """Uses the PostgresStore's connection (one statement per call; each statement is atomic)."""

    def __init__(self, store: Any) -> None:
        self.s = store

    def get_key(self, kid: str) -> dict | None:
        return self.s._one("select id, secret_hash, max_events, used, expires_at, revoked from diag_keys where id = %s", (kid,))

    def charge(self, kid: str, n: int, now: datetime) -> str:
        r = self.s._one("""update diag_keys set used = used + %s where id = %s and not revoked and expires_at > %s and used + %s <= max_events
                           returning used""", (n, kid, now, n))
        if r:
            return "ok"
        k = self.get_key(kid)
        return "expired_or_revoked" if (not k or k["revoked"] or k["expires_at"] <= now) else "quota"

    def insert(self, kid: str, events: list[dict], now: datetime) -> int:
        rows = []
        for e in events:
            rows.extend([e["attempt"], e["seq"], kid, e["nb"], e["marker"], e["utc"], json.dumps(e, separators=(",", ":")), now])
        values = ", ".join(["(%s, %s, %s, %s, %s, %s, %s::jsonb, %s)"] * len(events))
        got = self.s._run(f"insert into diag_events (attempt, seq, key_id, nb, marker, utc, payload, received_at) values {values} "
                          "on conflict (attempt, seq) do nothing returning seq", rows)
        return len(got or [])

    def prune(self, now: datetime, days: int = TTL_DAYS) -> int:
        r = self.s._run("with d as (delete from diag_events where received_at < %s returning 1) select count(*) as n from d", (now - timedelta(days=days),))
        return int(r[0]["n"]) if r else 0


def backend_for(store: Any) -> Any:
    from .store.postgres import PostgresStore
    return PostgresDiag(store) if isinstance(store, PostgresStore) else MemoryDiag()


# ---------------------------------------------------------------- route
router = APIRouter(prefix="/api/v1/diag", tags=["diagnostics"])


def _resp(status: int, body: dict) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={"cache-control": "no-store"})


def ingest(diag: Any, raw: bytes, authorization: str, now: datetime) -> tuple[int, dict, dict | None]:
    """Pure handler (no FastAPI): returns (status, body, log_record)."""
    try:
        if len(raw) > MAX_BODY:
            raise Reject(413, "too_large")
        try:
            body = json.loads(raw.decode("utf-8", "replace") or "null")
        except ValueError:
            raise Reject(400, "bad_json") from None
        events = body.get("events") if isinstance(body, dict) else None
        if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
            raise Reject(400, "bad_events")
        clean = [validate_event(e) for e in events]                       # validate BEFORE charging the quota
        m = _KEY_RE.match((authorization or "").removeprefix("Bearer ").removeprefix("bearer ").strip())
        if not m:
            raise Reject(401, "unauthorized")
        kid, secret = m.groups()
        k = diag.get_key(kid)
        if not (hmac.compare_digest(hash_secret(secret), k["secret_hash"] if k else "0" * 64) and k):
            raise Reject(401, "unauthorized")
        r = diag.charge(kid, len(clean), now)
        if r != "ok":
            # The secret already matched, so telling its holder WHY is no oracle for anyone else.
            raise Reject(401, "key_expired_or_revoked") if r == "expired_or_revoked" else Reject(429, "quota_exhausted")
        stored = diag.insert(kid, clean, now)
        return 200, {"stored": stored, "duplicates": len(clean) - stored}, {"diag": "ingest", "key": kid, "attempt": clean[0]["attempt"],
                                                                             "stored": stored, "markers": [e["marker"] for e in clean][:25]}
    except Reject as rj:
        return rj.status, {"error": rj.code}, {"diag": "reject", "status": rj.status, "code": rj.code}


@router.post("/events")
async def diag_events(request: Request, authorization: str | None = Header(default=None)):
    st = request.app.state.enigma
    diag = getattr(request.app.state, "diag", None)
    if diag is None:
        diag = request.app.state.diag = backend_for(st.store)
    raw = await request.body()
    try:
        status, body, rec = ingest(diag, raw, authorization or "", datetime.now(timezone.utc))
    except Exception as e:                                                # noqa: BLE001  failure is NOT an acknowledgement; never leak internals
        print(json.dumps({"diag": "error", "type": type(e).__name__}), flush=True)
        return _resp(503, {"error": "unavailable"})
    if rec:
        print(json.dumps(rec), flush=True)
    return _resp(status, body)
