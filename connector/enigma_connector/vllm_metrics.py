"""Scrape the LOCAL vLLM server's /metrics and parse it with ``prometheus_client.parser`` (the library's own exposition parser).

Only an allowlist of confirmed vLLM 0.25.1 families is kept (names verified against vllm/v1/metrics/loggers.py of the bundled
tree). Series for several ``engine``/``model_name`` label sets are aggregated (sum; KV usage uses the max). A family that is absent
stays ``None``: it is not turned into 0. Histograms stay CUMULATIVE (that is what Prometheus exposes); the API derives per-interval
quantiles. A counter going backwards bumps ``counter_epoch`` so downstream code never subtracts across a vLLM restart."""
from __future__ import annotations

import math
import urllib.request
from datetime import datetime, timezone
from typing import Any

from prometheus_client.parser import text_string_to_metric_families

GAUGES = {"num_requests_running": "vllm:num_requests_running", "num_requests_waiting": "vllm:num_requests_waiting"}
COUNTERS = {"prompt_tokens_total": "vllm:prompt_tokens", "generation_tokens_total": "vllm:generation_tokens", "preemptions_total": "vllm:num_preemptions"}
HISTS = {"ttft_hist": "vllm:time_to_first_token_seconds", "e2e_hist": "vllm:e2e_request_latency_seconds", "itl_hist": "vllm:inter_token_latency_seconds"}
KV = "vllm:kv_cache_usage_perc"        # fraction 0..1 ("1 means 100 percent")
SUCCESS = "vllm:request_success"
MAX_METRICS_BYTES = 8 * 1024 * 1024      # a vLLM exposition is ~100 KiB; never buffer an unbounded body


def _finite(v: float) -> bool:
    return not (math.isnan(v) or math.isinf(v))


def parse_exposition(text: str) -> dict[str, Any]:
    """Pure function: Prometheus text -> allowlisted fields (without timestamp/epoch)."""
    out: dict[str, Any] = {k: None for k in (*GAUGES, *COUNTERS, *HISTS)}
    out.update(kv_cache_usage_perc=None, request_success=None)
    buckets: dict[str, dict[float, float]] = {}
    sums: dict[str, float] = {}
    counts: dict[str, float] = {}
    succ: dict[str, float] = {}
    for fam in text_string_to_metric_families(text):
        for s in fam.samples:
            n, v, lab = s.name, float(s.value), s.labels
            if not _finite(v):
                continue
            for key, base in GAUGES.items():
                if n == base:
                    out[key] = (out[key] or 0.0) + v
            if n == KV:
                out["kv_cache_usage_perc"] = max(out["kv_cache_usage_perc"] if out["kv_cache_usage_perc"] is not None else 0.0, v)
            for key, base in COUNTERS.items():
                if n in (base, base + "_total"):
                    out[key] = (out[key] or 0.0) + v
            if n in (SUCCESS, SUCCESS + "_total"):
                succ[lab.get("finished_reason", "unknown")] = succ.get(lab.get("finished_reason", "unknown"), 0.0) + v
            for key, base in HISTS.items():
                if n == base + "_bucket":
                    le = float("inf") if lab.get("le") in ("+Inf", "inf", "Inf") else float(lab["le"])
                    b = buckets.setdefault(key, {})
                    b[le] = b.get(le, 0.0) + v
                elif n == base + "_sum":
                    sums[key] = sums.get(key, 0.0) + v
                elif n == base + "_count":
                    counts[key] = counts.get(key, 0.0) + v
    if succ:
        out["request_success"] = succ
    for key, b in buckets.items():
        finite = sorted(k for k in b if k != float("inf"))
        if float("inf") not in b or len(finite) > 64:
            continue                                            # malformed / oversized histogram: drop it rather than guess
        cum = [b[k] for k in finite] + [b[float("inf")]]
        if any(y < x for x, y in zip(cum, cum[1:])) or key not in sums or key not in counts:
            continue
        out[key] = {"le": finite, "cum": cum, "sum": sums[key], "count": counts[key]}
    return out


class VllmSampler:
    """Scrapes localhost, tracks counter resets. ``fetch`` is injectable for tests."""

    def __init__(self, base_url: str, timeout: float = 3.0, fetch=None):
        self.url = base_url.rstrip("/") + "/metrics"
        self.timeout = timeout
        self._fetch = fetch or self._http
        self.epoch = 0
        self._prev: dict[str, float] = {}
        self.last_error: str | None = None

    def _http(self) -> str:
        with urllib.request.urlopen(self.url, timeout=self.timeout) as r:      # noqa: S310 - localhost only (Config.validate)
            raw = r.read(MAX_METRICS_BYTES + 1)
        if len(raw) > MAX_METRICS_BYTES:
            raise ValueError("metrics response too large")
        return raw.decode("utf-8", "replace")

    def sample(self, now: datetime | None = None) -> dict | None:
        try:
            text = self._fetch()
            parsed = parse_exposition(text)
        except Exception as e:                                                  # unreachable server, malformed text: skip this tick
            self.last_error = type(e).__name__
            return None
        self.last_error = None
        cur: dict[str, float] = {k: parsed[k] for k in COUNTERS if parsed[k] is not None}
        for k in HISTS:
            if parsed[k] is not None:
                cur[k + ":count"] = parsed[k]["count"]
        if any(k in self._prev and v < self._prev[k] for k, v in cur.items()):
            self.epoch += 1
        self._prev = cur
        if all(parsed[k] is None for k in (*GAUGES, *COUNTERS, *HISTS, "kv_cache_usage_perc")):
            return None                                                         # no vllm: families at all: nothing to report
        return {"captured_at": (now or datetime.now(timezone.utc)).isoformat(), "counter_epoch": self.epoch, **parsed}
