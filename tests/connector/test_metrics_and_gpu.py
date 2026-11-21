"""Metric parsing (prometheus_client.parser), counter-reset epochs, NVML sampling with missing sensors, spool bounds, config safety."""
from __future__ import annotations

import json
import math
import random
import urllib.error
from pathlib import Path

import pytest

from enigma_connector.config import Config, ConfigError
from enigma_connector.gpu import NvmlSampler, NvmlUnavailable
from enigma_connector.http import ApiClient, ApiError, AuthError, Transient, backoff_delays, retry
from enigma_connector.spool import Spool
from enigma_connector.vllm_metrics import VllmSampler, parse_exposition

FIX = Path(__file__).parent / "fixtures" / "vllm_metrics_synthetic.txt"


# ------------------------------------------------------------------ vLLM metrics
def test_parse_allowlist_aggregates_engines_and_histograms():
    p = parse_exposition(FIX.read_text())
    assert p["num_requests_running"] == 3.0 and p["num_requests_waiting"] == 0.0           # summed across engines
    assert p["kv_cache_usage_perc"] == 0.25                                                  # max across engines, still a 0..1 fraction
    assert p["prompt_tokens_total"] == 1500.0 and p["generation_tokens_total"] == 4000.0 and p["preemptions_total"] == 3.0
    assert p["request_success"] == {"stop": 9.0, "length": 1.0}
    h = p["ttft_hist"]
    assert h["le"] == [0.01, 0.1, 1.0] and h["cum"] == [1.0, 8.0, 11.0, 12.0] and h["count"] == 12.0 and h["sum"] == pytest.approx(2.6)
    assert p["e2e_hist"] is None and p["itl_hist"] is None                                   # absent family stays None, not 0
    assert "request_prompt_tokens" not in json.dumps(p)                                      # allowlist: nothing else leaks through


def test_parse_missing_families_are_none_and_nan_ignored():
    p = parse_exposition('# TYPE vllm:num_requests_running gauge\nvllm:num_requests_running{engine="0"} NaN\n')
    assert p["num_requests_running"] is None and p["kv_cache_usage_perc"] is None and p["ttft_hist"] is None


def test_malformed_histograms_are_dropped_not_guessed():
    no_inf = '# TYPE vllm:time_to_first_token_seconds histogram\nvllm:time_to_first_token_seconds_bucket{le="1.0"} 3\nvllm:time_to_first_token_seconds_sum 1\nvllm:time_to_first_token_seconds_count 3\n'
    non_mono = ('# TYPE vllm:time_to_first_token_seconds histogram\nvllm:time_to_first_token_seconds_bucket{le="1.0"} 5\n'
                'vllm:time_to_first_token_seconds_bucket{le="+Inf"} 3\nvllm:time_to_first_token_seconds_sum 1\nvllm:time_to_first_token_seconds_count 3\n')
    assert parse_exposition(no_inf)["ttft_hist"] is None and parse_exposition(non_mono)["ttft_hist"] is None


def test_garbage_text_does_not_crash_sampler():
    s = VllmSampler("http://127.0.0.1:1", fetch=lambda: "this is {not prometheus")
    assert s.sample() is None and s.last_error is not None
    assert VllmSampler("http://127.0.0.1:1", fetch=lambda: "# nothing here\n").sample() is None        # no vllm: families -> nothing to report


def test_counter_reset_bumps_epoch_once():
    texts = iter([
        '# TYPE vllm:generation_tokens_total counter\nvllm:generation_tokens_total{engine="0"} 100\n',
        '# TYPE vllm:generation_tokens_total counter\nvllm:generation_tokens_total{engine="0"} 150\n',
        '# TYPE vllm:generation_tokens_total counter\nvllm:generation_tokens_total{engine="0"} 5\n',           # server restarted
        '# TYPE vllm:generation_tokens_total counter\nvllm:generation_tokens_total{engine="0"} 30\n'])
    s = VllmSampler("http://127.0.0.1:1", fetch=lambda: next(texts))
    assert [s.sample()["counter_epoch"] for _ in range(4)] == [0, 0, 1, 1]


def test_sampler_unreachable_server_returns_none():
    def boom():
        raise urllib.error.URLError("refused")
    s = VllmSampler("http://127.0.0.1:1", fetch=boom)
    assert s.sample() is None and s.last_error == "URLError"


# ------------------------------------------------------------------ NVML
class FakeNvmlError(Exception):
    pass


class NVMLError_NotSupported(FakeNvmlError):
    pass


class FakeNvml:
    NVML_TEMPERATURE_GPU = 0
    NVML_CLOCK_SM, NVML_CLOCK_MEM = 1, 2
    NVML_PCIE_UTIL_RX_BYTES, NVML_PCIE_UTIL_TX_BYTES = 0, 1

    def __init__(self, n=2, fail_init=False):
        self.n, self.fail_init, self.calls, self.shutdowns = n, fail_init, {}, 0

    def _c(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1

    def nvmlInit(self):
        if self.fail_init:
            raise FakeNvmlError("driver not loaded")

    def nvmlShutdown(self): self.shutdowns += 1
    def nvmlDeviceGetCount(self): return self.n
    def nvmlDeviceGetHandleByIndex(self, i): return i
    def nvmlSystemGetDriverVersion(self): return b"535.1"
    def nvmlDeviceGetUUID(self, h): return f"GPU-{h}".encode()
    def nvmlDeviceGetName(self, h): return b"Fake T4"

    class _U:
        gpu, memory = 37, 12

    class _M:
        used, total = 2048 * 1024 * 1024, 15360 * 1024 * 1024

    def nvmlDeviceGetUtilizationRates(self, h): return self._U()
    def nvmlDeviceGetMemoryInfo(self, h): return self._M()
    def nvmlDeviceGetTemperature(self, h, k): return 55
    def nvmlDeviceGetEnforcedPowerLimit(self, h): return 70000
    def nvmlDeviceGetClockInfo(self, h, k): return 585 if k == self.NVML_CLOCK_SM else 5001

    def nvmlDeviceGetPowerUsage(self, h):
        self._c("power")
        if h == 1:
            return 31500                                           # GPU1 reports power ...
        raise NVMLError_NotSupported("power")                      # ... GPU0 does not

    def nvmlDeviceGetPcieThroughput(self, h, k):
        self._c("pcie")
        raise NVMLError_NotSupported("pcie")


def test_nvml_sample_units_and_none_for_unsupported():
    nv = FakeNvml()
    s = NvmlSampler(nv)
    rows = s.sample()
    g0, g1 = rows
    assert g0["gpu_index"] == 0 and g1["gpu_index"] == 1 and g0["gpu_uuid"] == "GPU-0"
    assert g0["util_gpu_pct"] == 37.0 and g0["mem_used_mib"] == 2048.0 and g0["mem_total_mib"] == 15360.0 and g0["temp_c"] == 55.0
    assert g0["power_limit_w"] == 70.0 and g0["sm_clock_mhz"] == 585.0
    assert g0["power_w"] is None and g1["power_w"] == 31.5                                    # mW -> W, per-device None
    assert g0["pcie_rx_kib_s"] is None and g0["pcie_tx_kib_s"] is None                       # unsupported -> None, never 0
    assert {k for k, v in g0.items() if v is None} == {"power_w", "pcie_rx_kib_s", "pcie_tx_kib_s"}      # exactly the unsupported ones are None


def test_nvml_unsupported_counter_not_polled_again():
    nv = FakeNvml()
    s = NvmlSampler(nv)
    for _ in range(5):
        s.sample()
    assert nv.calls["pcie"] == 4          # rx and tx each fail once per GPU, then are never polled again: 2 GPUs x 2 counters
    assert nv.calls["power"] == 6         # GPU0: probed once then skipped (1); GPU1 supports it: polled on all 5 samples
    assert s.supported_counters()["pcie_rx"] is False and s.supported_counters()["power"] is True and s.supported_counters()["util"] is True


def test_nvml_inventory_and_close():
    nv = FakeNvml()
    s = NvmlSampler(nv)
    inv = s.inventory()
    assert inv[0] == {"index": 0, "uuid": "GPU-0", "name": "Fake T4", "memory_total_mib": 15360.0, "driver": "535.1"}
    s.close()
    s.close()
    assert nv.shutdowns == 1                                                                 # idempotent shutdown


def test_nvml_init_failure_is_reported_not_fatal():
    with pytest.raises(NvmlUnavailable):
        NvmlSampler(FakeNvml(fail_init=True))


def test_real_nvml_on_this_host_if_present():
    """Runs against the real NVIDIA driver when one exists (any GPU generation). Asserts shape + physical plausibility only."""
    try:
        s = NvmlSampler()
    except NvmlUnavailable:
        pytest.skip("no NVML on this host")
    try:
        inv, rows = s.inventory(), s.sample()
        assert len(inv) == len(rows) >= 1
        for r in rows:
            assert r["mem_total_mib"] is None or r["mem_total_mib"] > 0
            for k in ("util_gpu_pct", "util_mem_pct"):
                assert r[k] is None or 0 <= r[k] <= 100
            assert r["mem_used_mib"] is None or r["mem_used_mib"] <= r["mem_total_mib"]
            assert not any(isinstance(v, float) and math.isnan(v) for v in r.values())
    finally:
        s.close()


# ------------------------------------------------------------------ spool
def test_spool_orders_evicts_oldest_and_survives_corruption(tmp_path):
    sp = Spool(str(tmp_path), max_bytes=1500)
    for i in range(10):
        sp.put({"batch_id": f"b{i:08d}", "pad": "x" * 300})
    assert sp.size() <= 1500 and sp.dropped > 0
    first = sp.peek()[1]["batch_id"]
    assert first != "b00000000"                                           # oldest were evicted
    (tmp_path / "000000000001.json").write_text("{corrupt")               # a corrupt oldest file never blocks the queue
    got = []
    while (item := sp.peek()):
        got.append(item[1]["batch_id"])
        sp.ack(item[0])
    assert got == sorted(got) and len(sp) == 0
    assert not list(tmp_path.glob(".*.tmp"))                              # atomic writes leave no temp files


def test_spool_rejects_single_oversized_batch_and_resumes_numbering(tmp_path):
    sp = Spool(str(tmp_path), max_bytes=100)
    sp.put({"x": "y" * 500})
    assert len(sp) == 0 and sp.dropped == 1
    big = Spool(str(tmp_path), max_bytes=10_000)
    big.put({"a": 1})
    assert Spool(str(tmp_path), max_bytes=10_000)._n == big._n             # a restarted connector continues the sequence, preserving order


# ------------------------------------------------------------------ http / config
GOOD_TOKEN = "enig_rt_abcd1234_" + "A" * 43


def test_config_rules():
    assert Config(api_url="https://enigma.example", token=GOOD_TOKEN, instance_id="i1").validate()
    assert Config(api_url="http://127.0.0.1:8001", token=GOOD_TOKEN, instance_id="i1").validate()
    for bad in (dict(api_url="http://enigma.example"),                                            # plain http to a remote host
                dict(api_url="https://e.example", token="nope"),
                dict(api_url="https://e.example", vllm_url="http://0.0.0.0:8000"),               # vLLM must be loopback
                dict(api_url="https://e.example", vllm_url="http://10.0.0.5:8000"),
                dict(api_url="https://e.example", instance_id="bad id!"),
                dict(api_url="ftp://e.example")):
        with pytest.raises(ConfigError):
            Config(**{"token": GOOD_TOKEN, "instance_id": "i1", **bad}).validate()


def test_token_never_in_repr():
    c = Config(api_url="https://e.example", token=GOOD_TOKEN, instance_id="i1")
    assert GOOD_TOKEN not in repr(c) and "AAAA" not in repr(c) and "AAAA" not in str(c)


def test_backoff_is_bounded_and_jittered():
    d = backoff_delays(0.5, 30.0, random.Random(1))
    xs = [next(d) for _ in range(30)]
    assert max(xs) <= 30.0 and min(xs) >= 0 and len(set(xs)) > 20


def test_retry_only_transient():
    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise Transient("boom")
        return "ok"
    assert retry(flaky, sleep=sleeps.append) == "ok" and len(sleeps) == 2

    def auth():
        raise AuthError(401, "invalid_token")
    with pytest.raises(AuthError):
        retry(auth, sleep=sleeps.append)

    def always():
        raise Transient("x")
    with pytest.raises(Transient):
        retry(always, attempts=3, sleep=sleeps.append)


def test_api_client_error_mapping(monkeypatch):
    cfg = Config(api_url="http://127.0.0.1:9", token=GOOD_TOKEN, instance_id="i1")

    def opener_for(status, body):
        def op(req, timeout):
            assert req.headers["Authorization"] == f"Bearer {GOOD_TOKEN}" and GOOD_TOKEN not in req.full_url      # token only in the header
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, __import__("io").BytesIO(json.dumps(body).encode()))
        return op
    with pytest.raises(AuthError):
        ApiClient(cfg, opener_for(401, {"error": {"code": "invalid_token"}})).post("/x")
    with pytest.raises(Transient):
        ApiClient(cfg, opener_for(503, {"error": {"code": "store_unavailable"}})).post("/x")
    with pytest.raises(Transient):
        ApiClient(cfg, opener_for(429, {})).post("/x")
    with pytest.raises(ApiError) as e:
        ApiClient(cfg, opener_for(409, {"error": {"code": "sequence_gap", "expected_seq": 4, "correlation_id": "c"}})).post("/x")
    assert e.value.code == "sequence_gap" and e.value.extra == {"expected_seq": 4}

    def down(req, timeout):
        raise urllib.error.URLError("refused")
    with pytest.raises(Transient):
        ApiClient(cfg, down).post("/x")


def test_auto_spool_dir_is_removed_only_when_empty(tmp_path):
    sp = Spool(None)
    sp.put({"a": 1})
    assert sp.cleanup_if_empty() is False and sp.dir.exists()          # undelivered data is never discarded
    path, _ = sp.peek()
    sp.ack(path)
    assert sp.cleanup_if_empty() is True and not sp.dir.exists()
    chosen = Spool(str(tmp_path / "mine"))
    assert chosen.cleanup_if_empty() is False and chosen.dir.exists()   # a user-chosen directory is never removed
