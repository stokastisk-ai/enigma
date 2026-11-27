"""POST /api/v1/diag/events (replacement for the AWS diag-ingest Lambda) on the in-process backend. Unit evidence only; the PostgreSQL backend is
exercised by the deploy smoke test (deploy/oci) against a real database."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from enigma_api.diag import MemoryDiag, hash_secret, ingest
from enigma_api.main import create_app
from enigma_api.settings import Settings
from enigma_api.store.memory import MemoryStore

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


def key(diag: MemoryDiag, kid="abcd1234", max_events=50, expires=NOW + timedelta(days=1)) -> str:
    secret = secrets.token_urlsafe(32)[:43].ljust(43, "A")
    diag.add_key(kid, hash_secret(secret), "test", max_events, expires)
    return f"Bearer edx_{kid}_{secret}"


def ev(seq=1, **kw) -> dict:
    return {"attempt": "nb03-attempt-1", "nb": "03", "seq": seq, "marker": "pre_gpu", "utc": "2026-11-27T12:00:00Z", **kw}


def body(*events) -> bytes:
    return json.dumps({"events": list(events)}).encode()


def test_store_and_idempotent_duplicates():
    d = MemoryDiag()
    h = key(d)
    assert ingest(d, body(ev(1), ev(2)), h, NOW)[:2] == (200, {"stored": 2, "duplicates": 0})
    assert ingest(d, body(ev(2), ev(3)), h, NOW)[:2] == (200, {"stored": 1, "duplicates": 1})
    assert d.events[("nb03-attempt-1", 2)]["marker"] == "pre_gpu"            # never overwritten


@pytest.mark.parametrize("auth", ["", "Bearer x", "Bearer edx_abcd1234_" + "B" * 43, "Bearer edx_zzzz9999_" + "A" * 43, "Bearer enig_rt_abcd1234_x"])
def test_uniform_401(auth):
    d = MemoryDiag()
    key(d)
    assert ingest(d, body(ev()), auth, NOW)[:2] == (401, {"error": "unauthorized"})


def test_quota_expiry_and_revocation():
    d = MemoryDiag()
    h = key(d, max_events=3)
    assert ingest(d, body(ev(1), ev(2)), h, NOW)[0] == 200
    assert ingest(d, body(ev(3), ev(4)), h, NOW)[:2] == (429, {"error": "quota_exhausted"})
    assert d.keys["abcd1234"]["used"] == 2                                 # a refused charge consumes nothing
    h2 = key(d, kid="exp00001", expires=NOW - timedelta(seconds=1))
    assert ingest(d, body(ev()), h2, NOW)[:2] == (401, {"error": "key_expired_or_revoked"})
    h3 = key(d, kid="rev00001")
    d.keys["rev00001"]["revoked"] = True
    assert ingest(d, body(ev()), h3, NOW)[:2] == (401, {"error": "key_expired_or_revoked"})


@pytest.mark.parametrize("bad,code", [
    (ev(prompt="hello"), "unknown_field"),
    ({k: v for k, v in ev().items() if k != "marker"}, "missing_field"),
    (ev(note="Authorization: Bearer abcdefghijklmnop"), "bad_field"),
    (ev(note="postgresql://u:p@h/db"), "bad_field"),
    (ev(note="enig_rt_abcd1234_secret"), "bad_field"),
    (ev(note="ocid1.tenancy.oc1..aaaa"), "bad_field"),
    (ev(seq=True), "bad_field"),
    (ev(attempt="has space"), "bad_identity"),
    (ev(gpu_util_pct=[1] * 9), "bad_field"),
])
def test_validation_rejects_before_charging(bad, code):
    d = MemoryDiag()
    h = key(d, max_events=5)
    assert ingest(d, body(bad), h, NOW)[:2] == (400, {"error": code})
    assert d.keys["abcd1234"]["used"] == 0 and not d.events


def test_size_and_count_limits():
    d = MemoryDiag()
    h = key(d, max_events=1000)
    assert ingest(d, body(*[ev(i) for i in range(26)]), h, NOW)[1] == {"error": "bad_events"}
    assert ingest(d, b"x" * (32 * 1024 + 1), h, NOW)[1] == {"error": "too_large"}
    assert ingest(d, b"{not json", h, NOW)[1] == {"error": "bad_json"}


def test_retention_prune():
    d = MemoryDiag()
    h = key(d)
    ingest(d, body(ev(1)), h, NOW - timedelta(days=31))
    ingest(d, body(ev(2)), h, NOW)
    assert d.prune(NOW) == 1 and list(d.events) == [("nb03-attempt-1", 2)]


def test_route_is_mounted_and_separate_from_runtime_auth():
    app = create_app(Settings(env="test", cron_secret="c" * 32), store=MemoryStore())
    c = TestClient(app)
    h = key(app.state.diag)
    r = c.post("/api/v1/diag/events", content=body(ev()), headers={"authorization": h, "content-type": "application/json"})
    assert r.status_code == 200 and r.json() == {"stored": 1, "duplicates": 0} and r.headers["cache-control"] == "no-store"
    # a diagnostic key is not a runtime token
    assert c.post("/api/v1/jobs/claim", json={}, headers={"authorization": h}).status_code == 401
    pr = c.get("/api/v1/admin/prune", headers={"authorization": "Bearer " + "c" * 32})
    assert pr.status_code == 200 and "diag_events" in pr.json()


def _prune_app(hold: str):
    from enigma_api.store.memory import MemoryStore as _MS
    st = _MS()
    calls = []
    orig = st.prune
    st.prune = lambda **kw: (calls.append(kw), orig(**kw))[1]
    app = create_app(Settings(env="test", cron_secret="c" * 32, retention_hold_until=hold), store=st)
    return TestClient(app), calls


def test_retention_hold_keeps_events_only_while_active_and_is_bounded():
    now = datetime.now(timezone.utc)
    c, calls = _prune_app((now + timedelta(days=3)).isoformat())
    r = c.get("/api/v1/admin/prune", headers={"authorization": "Bearer " + "c" * 32}).json()
    assert calls[-1] == {"event_hours": 87600} and r["events_retention_hold_until"]
    c, calls = _prune_app((now - timedelta(minutes=1)).isoformat())                      # expired: normal 48 h retention resumes by itself
    r = c.get("/api/v1/admin/prune", headers={"authorization": "Bearer " + "c" * 32}).json()
    assert calls[-1] == {} and r["events_retention_hold_until"] is None
    for bad in ((now + timedelta(days=8)).isoformat(), "2026-12-01", "not-a-date"):     # > 7 days, no timezone, garbage
        assert any("ENIGMA_RETENTION_HOLD_UNTIL" in p for p in Settings(env="test", retention_hold_until=bad).validate_for_startup())
