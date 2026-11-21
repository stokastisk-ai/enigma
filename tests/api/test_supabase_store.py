"""SupabaseStore / SupabaseBroadcaster against a MOCKED transport: request shape, pagination, error mapping.
This does NOT prove behaviour on a live Supabase project (BLOCKED_EXTERNAL_SETUP); the SQL it calls is tested in tests/sql."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from enigma_api.main import create_app
from enigma_api.realtime import SupabaseBroadcaster
from enigma_api.settings import Settings
from enigma_api.store.supabase import StoreUnavailable, SupabaseStore

URL, KEY = "https://proj.supabase.co", "service-key-123"


def make(handler):
    return SupabaseStore(URL, KEY, transport=httpx.MockTransport(handler))


def test_rpc_shape_and_auth_headers():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(200, json={"ok": True, "job_id": "j1"})

    res = make(handler).submit_job("w", "c", "r", "u", "idem", "hi", {"max_tokens": 8, "temperature": 0.0}, 100)
    req = seen[0]
    assert res["job_id"] == "j1" and req.method == "POST" and str(req.url) == f"{URL}/rest/v1/rpc/enigma_submit_job"
    assert req.headers["apikey"] == KEY and req.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(req.content)["p_idem"] == "idem" and KEY not in str(req.url)


def test_claim_returns_first_row_or_none():
    assert make(lambda r: httpx.Response(200, json=[{"id": "j1"}])).claim_job("r", "o", 30) == {"id": "j1"}
    assert make(lambda r: httpx.Response(200, json=[])).claim_job("r", "o", 30) is None


def test_select_uses_eq_filter_and_range_pagination():
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        lo, hi = map(int, req.headers["range"].split("-"))
        return httpx.Response(200, json=[{"i": i} for i in range(lo, min(hi, 1499) + 1)])

    rows = make(handler)._many("gpu_samples", {"project_id": "eq.p"}, 5000)
    assert len(rows) == 1500 and len(calls) == 2                                        # 1000 + 500 then a short page stops the loop
    assert calls[0].headers["range"] == "0-999" and calls[1].headers["range"] == "1000-1999"
    assert calls[0].url.params["project_id"] == "eq.p"


def test_time_window_filter():
    seen = []
    make(lambda r: (seen.append(r), httpx.Response(200, json=[]))[1]).query_gpu(
        "p", "rt", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc), 10)
    q = seen[0].url.params
    assert q["runtime_id"] == "eq.rt" and q["order"] == "captured_at.asc" and "captured_at.gte.2026-01-01" in q["and"]


def test_http_errors_become_store_unavailable_without_leaking_body():
    store = make(lambda r: httpx.Response(500, text="SECRET-ROW-DATA prompt text"))
    with pytest.raises(StoreUnavailable) as e:
        store.get_project("x")
    assert "SECRET" not in str(e.value) and "500" in str(e.value)

    def boom(req):
        raise httpx.ConnectTimeout("timed out")
    with pytest.raises(StoreUnavailable):
        make(boom).get_project("x")


def test_api_maps_store_outage_to_503_with_correlation_id():
    store = make(lambda r: httpx.Response(503, text="down"))
    s = Settings(env="test", dev_auth=True, dev_jwt_secret="d" * 48)
    from api_helpers import dev_token
    with TestClient(create_app(s, store=store), raise_server_exceptions=False) as c:
        r = c.get("/api/v1/app/workspaces", headers={"Authorization": f"Bearer {dev_token('u')}"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "store_unavailable" and r.headers["retry-after"] == "5"


def test_listing_credentials_never_selects_secret_hash():
    seen = []
    make(lambda r: (seen.append(r), httpx.Response(200, json=[]))[1]).list_credentials("rt")
    assert "secret_hash" not in seen[0].url.params["select"]


def test_broadcaster_targets_private_topic_and_survives_failure():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(202)
    b = SupabaseBroadcaster(URL, KEY, transport=httpx.MockTransport(handler))
    assert b.send("conv-1", "events", {"job_id": "j", "events": []}) is True
    body = json.loads(seen[0].content)["messages"][0]
    assert str(seen[0].url) == f"{URL}/realtime/v1/api/broadcast" and body["topic"] == "conv:conv-1" and body["private"] is True

    def boom(req):
        raise httpx.ConnectError("nope")
    assert SupabaseBroadcaster(URL, KEY, transport=httpx.MockTransport(boom)).send("c", "e", {}) is False       # best-effort: never raises
