"""The threaded Connector end to end against the live API (mock mode): register, heartbeat, telemetry, jobs, offline spool, revocation."""
from __future__ import annotations

import time
import urllib.error
import urllib.request

import httpx
import pytest

from enigma_connector.http import ApiClient, ApiError
from enigma_connector.mock import MockGpu, MockLlm, MockVllm
from enigma_connector.runner import Connector
from enigma_connector.spool import Spool


def make(cfg, api, tmp_path, **kw):
    mv = MockVllm()
    return Connector(cfg, api, MockLlm(0.001, 0.001, vllm=mv), MockGpu(), mv, Spool(str(tmp_path / "spool")), is_mock=True, **kw)


def wait_for(fn, timeout=10.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condition not met in time")


def test_full_mock_lifecycle(live, tenant, cfg, api, tmp_path):
    c = make(cfg, api, tmp_path)
    c.start()
    try:
        base, h = live.url, tenant.h
        conv = tenant.conversation()
        wait_for(lambda: httpx.get(f"{base}/api/v1/app/projects/{tenant.project_id}/runtimes", headers=h).json()["items"][0]["status"] == "online")
        job_id = tenant.submit(conv, "tell me about gpus").json()["job_id"]
        v = wait_for(lambda: (j := httpx.get(f"{base}/api/v1/app/jobs/{job_id}", headers=h).json())["status"] == "succeeded" and j)
        assert v["result"]["is_mock"] is True
        msgs = httpx.get(f"{base}/api/v1/app/conversations/{conv}/messages", headers=h).json()["items"]
        assert msgs[-1]["content"].startswith("[MOCK MODEL")
        # telemetry: GPU 0 and 1 present, flagged mock, vLLM counters moved with the (fake) request
        g = wait_for(lambda: (m := httpx.get(f"{base}/api/v1/app/projects/{tenant.project_id}/metrics/gpu?range=5m", headers=h).json())["series"] and m)
        assert set(g["series"]) == {"0", "1"} and g["provenance"]["measured_rows"] == 0 and g["provenance"]["mock_rows"] > 0
        vl = wait_for(lambda: (m := httpx.get(f"{base}/api/v1/app/projects/{tenant.project_id}/metrics/vllm?range=5m", headers=h).json())["points"] and m)
        assert vl["provenance"]["mock_rows"] == vl["provenance"]["rows"]
        assert c.stats["batches_sent"] >= 1 and c.stats["jobs_done"] >= 1
    finally:
        c.stop()
    assert not c.fatal and all(not t.is_alive() for t in c._threads)


def test_mock_connector_cannot_use_a_real_runtime_credential(live, cfg, api, tmp_path):
    import httpx as hx
    from api_helpers import Tenant
    class Http:
        def __init__(self, base): self.c = hx.Client(base_url=base, timeout=10)
        def post(self, url, **kw): return self.c.post(url, **kw)
    real = Tenant(Http(live.url), "user_real")
    real.create_runtime("real-rt", is_mock=False)
    from dataclasses import replace
    c = make(replace(cfg, token=real.token), ApiClient(replace(cfg, token=real.token)), tmp_path)
    with pytest.raises(ApiError) as e:
        c.register()
    assert e.value.code == "mock_mismatch"


def test_offline_spool_then_ordered_replay_without_duplicates(live, tenant, cfg, tmp_path):
    state = {"offline": True}
    real = urllib.request.urlopen

    def opener(req, timeout):
        if state["offline"]:
            raise urllib.error.URLError("network down")
        return real(req, timeout=timeout)
    api = ApiClient(cfg, opener)
    state["offline"] = False
    c = make(cfg, api, tmp_path)
    c.register()
    state["offline"] = True
    from datetime import datetime, timedelta, timezone
    base_t = datetime.now(timezone.utc) - timedelta(minutes=5)
    for k in range(3):                                                                  # three telemetry windows collected while offline
        c._gpu_buf.extend(c.gpu.sample(base_t + timedelta(seconds=10 * k)))
        c._upload_telemetry()
    assert len(c.spool) == 3 and c.stats["batches_spooled"] == 3 and c.stats["batches_sent"] == 0
    state["offline"] = False                                                            # reconnect
    c._gpu_buf.extend(c.gpu.sample(base_t + timedelta(seconds=40)))
    c._upload_telemetry()
    assert len(c.spool) == 0 and c.stats["batches_sent"] == 4
    rows = httpx.get(f"{live.url}/api/v1/app/projects/{tenant.project_id}/metrics/gpu?range=1h&max_points=600", headers=tenant.h).json()
    assert [p["samples"] for p in rows["series"]["0"]] == [1, 1, 1, 1] and len(rows["series"]["1"]) == 4       # all windows arrived, none duplicated
    c._upload_telemetry()
    assert c.stats["batches_sent"] == 4                                                 # nothing left to send
    # replaying a spooled batch that was already delivered (response lost) is a server-side no-op
    batch = {"batch_id": "dup-batch-0001", "run_id": c.run_id, "gpu": c.gpu.sample(base_t + timedelta(seconds=50)), "vllm": []}
    assert api.post("/api/v1/telemetry/batch", batch)["gpu_rows"] == 2
    assert api.post("/api/v1/telemetry/batch", batch)["duplicate"] is True


def test_poison_batch_is_dropped_and_does_not_block_queue(live, tenant, cfg, api, tmp_path):
    c = make(cfg, api, tmp_path)
    c.register()
    c.spool.put({"batch_id": "poison-batch-01", "run_id": c.run_id, "gpu": [{"captured_at": "2020-01-01T00:00:00+00:00", "gpu_index": 0}], "vllm": []})   # too old: 422
    c.spool.put({"batch_id": "good-batch-0001", "run_id": c.run_id, "gpu": c.gpu.sample(), "vllm": []})
    c._upload_telemetry()
    assert len(c.spool) == 0 and c.stats["batches_dropped"] == 1 and c.stats["batches_sent"] == 1


def test_revoked_token_stops_connector_cleanly(live, tenant, cfg, api, tmp_path):
    c = make(cfg, api, tmp_path)
    c.start()
    try:
        wait_for(lambda: c.stats["batches_sent"] >= 1)
        httpx.post(f"{live.url}/api/v1/app/runtimes/{tenant.runtime_id}/revoke", headers=tenant.h)
        wait_for(lambda: c.stop_event.is_set(), timeout=10)
        assert c.fatal is not None and getattr(c.fatal, "status", None) in (401, 403)
    finally:
        c.stop()


def test_heartbeat_failure_does_not_kill_connector(live, tenant, cfg, tmp_path):
    state = {"fail": False}
    real = urllib.request.urlopen

    def opener(req, timeout):
        if state["fail"] and "heartbeat" in req.full_url:
            raise urllib.error.URLError("blip")
        return real(req, timeout=timeout)
    c = make(cfg, ApiClient(cfg, opener), tmp_path)
    c.start()
    try:
        state["fail"] = True
        wait_for(lambda: c.stats["heartbeats_failed"] >= 2)
        assert not c.stop_event.is_set()
        state["fail"] = False
        n = c.stats["heartbeats_failed"]
        time.sleep(0.7)
        assert c.stats["heartbeats_failed"] == n and not c.stop_event.is_set()          # recovered by itself
    finally:
        c.stop()
