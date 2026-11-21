"""Telemetry ingestion + dashboard views: null stays null, mock is labelled, idempotency, tenancy, bounded sizes."""
from __future__ import annotations

from datetime import timedelta

ISO = lambda t: t.isoformat()                                                              # noqa: E731


def gpu(ts, idx=0, **kw):
    return {"captured_at": ISO(ts), "gpu_index": idx, **kw}


def hist(le, cum, total_sum):
    return {"le": le, "cum": cum, "sum": total_sum, "count": cum[-1]}


def post(client, t, batch_id, gpu_rows=(), vllm_rows=(), **kw):
    return client.post("/api/v1/telemetry/batch", headers=t.rh, json={"batch_id": batch_id, "run_id": getattr(t, "run_id", None), "gpu": list(gpu_rows), "vllm": list(vllm_rows), **kw})


def test_missing_sensors_stay_null_not_zero(client, clock, alice):
    now = clock()
    r = post(client, alice, "batch-0001", [gpu(now - timedelta(seconds=2), util_gpu_pct=55.0, mem_used_mib=1000, mem_total_mib=15360, temp_c=None, power_w=None)])
    assert r.json() == {"ok": True, "duplicate": False, "gpu_rows": 1, "vllm_rows": 0}
    m = client.get(f"/api/v1/app/projects/{alice.project_id}/metrics/gpu?range=5m", headers=alice.h).json()
    pt = m["series"]["0"][0]
    assert pt["util_gpu_pct"] == 55.0 and pt["temp_c"] is None and pt["power_w"] is None and pt["pcie_rx_kib_s"] is None
    assert m["latest"]["0"]["temp_c"] is None and m["latest"]["0"]["age_s"] == 2.0


def test_mock_rows_are_labelled_in_provenance(client, clock, alice):
    post(client, alice, "batch-0002", [gpu(clock() - timedelta(seconds=1), util_gpu_pct=1.0)])
    m = client.get(f"/api/v1/app/projects/{alice.project_id}/metrics/gpu", headers=alice.h).json()
    assert m["provenance"] == {"rows": 1, "mock_rows": 1, "measured_rows": 0} and m["latest"]["0"]["is_mock"] is True


def test_batch_replay_is_idempotent(client, clock, alice):
    rows = [gpu(clock() - timedelta(seconds=5), util_gpu_pct=10.0)]
    assert post(client, alice, "batch-0003", rows).json()["gpu_rows"] == 1
    again = post(client, alice, "batch-0003", rows).json()
    assert again["duplicate"] is True and again["gpu_rows"] == 0
    # same sample re-sent under a new batch id (spool retry): unique(runtime, gpu, ts) drops it
    assert post(client, alice, "batch-0004", rows).json()["gpu_rows"] == 0


def test_validation_bounds(client, clock, alice):
    now = clock()
    bad = [dict(util_gpu_pct=101), dict(util_gpu_pct=-1), dict(mem_used_mib=-5), dict(temp_c=900), dict(power_w=-1), dict(bogus=1)]
    for kw in bad:
        assert post(client, alice, "batch-bad-001", [gpu(now - timedelta(seconds=1), **kw)]).status_code == 422, kw
    assert post(client, alice, "batch-bad-002", [gpu(now + timedelta(minutes=10))]).status_code == 422                # future
    assert post(client, alice, "batch-bad-003", [gpu(now - timedelta(hours=30))]).status_code == 422                   # too old
    assert post(client, alice, "short").status_code == 422                                                              # batch id too short
    assert post(client, alice, "batch-bad-004", [gpu(now - timedelta(seconds=i)) for i in range(601)]).status_code == 422   # > 600 rows
    h = hist([0.1, 0.5], [1, 0, 3], 1)                                                                                   # non-monotonic cumulative
    assert post(client, alice, "batch-bad-005", vllm_rows=[{"captured_at": ISO(now - timedelta(seconds=1)), "ttft_hist": h}]).status_code == 422
    h = hist([0.1, 0.5], [1, 2], 1)                                                                                      # wrong length
    assert post(client, alice, "batch-bad-006", vllm_rows=[{"captured_at": ISO(now - timedelta(seconds=1)), "ttft_hist": h}]).status_code == 422


def test_unknown_run_rejected(client, clock, alice):
    r = client.post("/api/v1/telemetry/batch", headers=alice.rh, json={"batch_id": "batch-0005", "run_id": "00000000-0000-0000-0000-000000000000", "gpu": []})
    assert r.status_code == 404 and r.json()["error"]["code"] == "run_not_found"


def test_tenant_isolation_of_metrics(client, clock, alice, bob):
    post(client, alice, "batch-0006", [gpu(clock() - timedelta(seconds=1), util_gpu_pct=99.0)])
    assert client.get(f"/api/v1/app/projects/{bob.project_id}/metrics/gpu", headers=bob.h).json()["series"] == {}
    assert client.get(f"/api/v1/app/projects/{alice.project_id}/metrics/gpu?runtime_id={bob.runtime_id}", headers=alice.h).status_code == 404


def test_vllm_series_rates_quantiles_and_counter_reset(client, clock, alice):
    t0 = clock() - timedelta(seconds=60)
    le = [0.05, 0.1, 0.5, 1.0]

    def row(i, prompt, gen, ttft_cum, epoch=0):
        return {"captured_at": ISO(t0 + timedelta(seconds=10 * i)), "counter_epoch": epoch, "num_requests_running": 1, "num_requests_waiting": 0,
                "kv_cache_usage_perc": 0.25, "prompt_tokens_total": prompt, "generation_tokens_total": gen, "preemptions_total": 0,
                "ttft_hist": hist(le, ttft_cum, 1.0)}
    rows = [row(0, 100, 50, [0, 0, 0, 0, 0]),
            row(1, 200, 150, [0, 10, 10, 10, 10]),             # 10 obs in (0.05, 0.1] -> p50 = 0.075
            row(2, 300, 250, [0, 10, 20, 20, 20]),             # 10 obs in (0.1, 0.5]  -> p50 = 0.3
            row(3, 5, 7, [0, 0, 1, 1, 1], epoch=1)]            # vLLM restarted: counters went backwards, new epoch
    assert post(client, alice, "batch-0007", vllm_rows=rows).json()["vllm_rows"] == 4
    pts = client.get(f"/api/v1/app/projects/{alice.project_id}/metrics/vllm?range=5m", headers=alice.h).json()["points"]
    assert pts[0]["generation_tokens_per_s"] is None and pts[0]["ttft_p50_s"] is None             # no previous sample: unknown, not 0
    assert pts[1]["prompt_tokens_per_s"] == 10.0 and pts[1]["generation_tokens_per_s"] == 10.0
    assert abs(pts[1]["ttft_p50_s"] - 0.075) < 1e-9 and abs(pts[2]["ttft_p50_s"] - 0.3) < 1e-9
    assert pts[3]["counter_reset"] is True and pts[3]["generation_tokens_per_s"] is None and pts[3]["ttft_p50_s"] is None
    assert pts[0]["kv_cache_usage_perc"] == 0.25


def test_run_summary_uses_epoch_aware_totals(client, clock, alice):
    clock.advance(60)                                    # samples must fall inside the run window (after registration)
    t0 = clock() - timedelta(seconds=40)
    le = [0.1, 1.0]
    mk = lambda i, gen, c, ep: {"captured_at": ISO(t0 + timedelta(seconds=10 * i)), "counter_epoch": ep, "generation_tokens_total": gen,    # noqa: E731
                                "ttft_hist": hist(le, c, 1.0), "kv_cache_usage_perc": 0.5}
    post(client, alice, "batch-0008", [gpu(t0 + timedelta(seconds=10 * i), util_gpu_pct=u, mem_used_mib=100 * (i + 1), temp_c=None) for i, u in enumerate([10, 30])],
         [mk(0, 0, [0, 0, 0], 0), mk(1, 100, [5, 5, 5], 0), mk(2, 5, [0, 0, 0], 1), mk(3, 55, [2, 2, 2], 1)])
    s = client.get(f"/api/v1/app/runs/{alice.run_id}", headers=alice.h).json()
    assert s["summary"]["generation_tokens"] == 150.0                     # 100 before the restart + 50 after; the backwards jump is not subtracted
    assert s["summary"]["gpus"]["0"]["util_gpu_avg_pct"] == 20.0 and s["summary"]["gpus"]["0"]["temp_max_c"] is None
    assert s["summary"]["ttft_observations"] == 7.0 and s["summary"]["kv_cache_max_perc"] == 50.0
    assert s["provenance"]["mock_rows"] == s["provenance"]["rows"]


def test_run_aborted_when_runtime_goes_silent(client, clock, alice):
    runs = client.get(f"/api/v1/app/projects/{alice.project_id}/runs", headers=alice.h).json()["items"]
    assert runs[0]["status"] == "active" and runs[0]["is_mock"] is True
    clock.advance(300)
    runs = client.get(f"/api/v1/app/projects/{alice.project_id}/runs", headers=alice.h).json()["items"]
    assert runs[0]["status"] == "aborted" and runs[0]["ended_at"] is not None


def test_overview_shows_status_and_staleness(client, clock, alice):
    post(client, alice, "batch-0009", [gpu(clock() - timedelta(seconds=3), idx=0, util_gpu_pct=5.0), gpu(clock() - timedelta(seconds=3), idx=1, util_gpu_pct=7.0)])
    o = client.get(f"/api/v1/app/projects/{alice.project_id}/overview", headers=alice.h).json()
    assert o["runtimes"][0]["status"] == "online" and o["runtimes"][0]["is_mock"] is True
    assert [(g["gpu_index"], g["age_s"]) for g in o["gpus"]] == [(0, 3.0), (1, 3.0)]
    clock.advance(500)
    o = client.get(f"/api/v1/app/projects/{alice.project_id}/overview", headers=alice.h).json()
    assert o["runtimes"][0]["status"] == "offline" and o["gpus"] == []                  # old samples age out of the live view instead of looking current


def test_invalid_range_and_registration_metadata(client, alice):
    assert client.get(f"/api/v1/app/projects/{alice.project_id}/metrics/gpu?range=3y", headers=alice.h).status_code == 422
    rt = client.get(f"/api/v1/app/projects/{alice.project_id}/runtimes", headers=alice.h).json()["items"][0]
    assert rt["served_model"] == "mock-model" and rt["capabilities"]["gpus"][0]["uuid"] == "GPU-MOCK-0"
