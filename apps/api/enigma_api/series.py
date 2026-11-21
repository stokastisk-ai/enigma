"""Pure time-series maths for dashboards. No I/O.

Rules: a missing value stays None (never 0); counters are only differenced inside one ``counter_epoch`` and a negative delta is
treated as a reset (None), not as a huge negative rate; histogram quantiles follow Prometheus' histogram_quantile (linear
interpolation inside the bucket; a rank in the +Inf bucket returns the highest finite bound)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

GPU_FIELDS = ("util_gpu_pct", "util_mem_pct", "mem_used_mib", "mem_total_mib", "temp_c", "power_w", "power_limit_w",
              "sm_clock_mhz", "mem_clock_mhz", "pcie_rx_kib_s", "pcie_tx_kib_s")


def _ts(v: Any) -> datetime:
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v


def histogram_quantile(q: float, le: list[float], cum: list[float]) -> float | None:
    """cum has len(le)+1 entries (last = +Inf bucket = total). Returns None when there are no observations."""
    if not 0 <= q <= 1 or len(cum) != len(le) + 1 or not cum:
        return None
    total = cum[-1]
    if total <= 0:
        return None
    rank = q * total
    i = next((k for k, c in enumerate(cum) if c >= rank), len(cum) - 1)
    if i == len(cum) - 1:
        return le[-1] if le else None
    prev = cum[i - 1] if i > 0 else 0.0
    start = le[i - 1] if i > 0 else 0.0
    in_bucket = cum[i] - prev
    if in_bucket <= 0:
        return le[i]
    return start + (le[i] - start) * (rank - prev) / in_bucket


def hist_delta(a: dict | None, b: dict | None) -> dict | None:
    """b - a for two cumulative histograms with identical bucket layout; None if not comparable or a reset happened."""
    if not a or not b or a["le"] != b["le"]:
        return None
    cum = [y - x for x, y in zip(a["cum"], b["cum"])]
    if any(c < 0 for c in cum) or b["count"] < a["count"]:
        return None
    return {"le": b["le"], "cum": cum, "sum": b["sum"] - a["sum"], "count": b["count"] - a["count"]}


def bucket_gpu(rows: list[dict], since: datetime, until: datetime, max_points: int) -> dict[int, list[dict]]:
    """Average measured values per GPU into <= max_points time buckets. A bucket where every sample lacked a field reports None."""
    span = max((until - since).total_seconds(), 1.0)
    width = max(span / max(max_points, 1), 1.0)
    out: dict[int, dict[int, dict]] = {}
    for r in rows:
        t = _ts(r["captured_at"])
        k = int((t - since).total_seconds() // width)
        cell = out.setdefault(r["gpu_index"], {}).setdefault(k, {"n": 0, "_sum": {f: 0.0 for f in GPU_FIELDS}, "_cnt": {f: 0 for f in GPU_FIELDS}, "t0": t, "uuid": r.get("gpu_uuid")})
        cell["n"] += 1
        for f in GPU_FIELDS:
            v = r.get(f)
            if v is not None:
                cell["_sum"][f] += v
                cell["_cnt"][f] += 1
    series: dict[int, list[dict]] = {}
    for gpu, cells in out.items():
        pts = []
        for k in sorted(cells):
            c = cells[k]
            pt = {"t": c["t0"].isoformat(), "samples": c["n"], "gpu_uuid": c["uuid"]}
            for f in GPU_FIELDS:
                pt[f] = c["_sum"][f] / c["_cnt"][f] if c["_cnt"][f] else None
            pts.append(pt)
        series[gpu] = pts
    return series


def vllm_points(rows: list[dict]) -> list[dict]:
    """Derive plottable points from cumulative vLLM samples (rows sorted by captured_at)."""
    pts: list[dict] = []
    prev: dict | None = None
    for r in rows:
        t = _ts(r["captured_at"])
        pt: dict[str, Any] = {"t": t.isoformat(), "num_requests_running": r.get("num_requests_running"), "num_requests_waiting": r.get("num_requests_waiting"),
                              "kv_cache_usage_perc": r.get("kv_cache_usage_perc"), "prompt_tokens_per_s": None, "generation_tokens_per_s": None,
                              "preemptions_in_interval": None, "ttft_p50_s": None, "ttft_p95_s": None, "e2e_p50_s": None, "e2e_p95_s": None,
                              "itl_p50_s": None, "itl_p95_s": None, "requests_in_interval": None, "counter_reset": False}
        if prev is not None:
            dt = (t - _ts(prev["captured_at"])).total_seconds()
            if r.get("counter_epoch", 0) != prev.get("counter_epoch", 0):
                pt["counter_reset"] = True                    # server restarted: no differences across the boundary
            elif dt > 0:
                for src, dst in (("prompt_tokens_total", "prompt_tokens_per_s"), ("generation_tokens_total", "generation_tokens_per_s")):
                    a, b = prev.get(src), r.get(src)
                    if a is not None and b is not None:
                        if b >= a:
                            pt[dst] = (b - a) / dt
                        else:
                            pt["counter_reset"] = True
                a, b = prev.get("preemptions_total"), r.get("preemptions_total")
                if a is not None and b is not None and b >= a:
                    pt["preemptions_in_interval"] = b - a
                for name in ("ttft", "e2e", "itl"):
                    d = hist_delta(prev.get(f"{name}_hist"), r.get(f"{name}_hist"))
                    if d and d["count"] > 0:
                        pt[f"{name}_p50_s"] = histogram_quantile(0.5, d["le"], d["cum"])
                        pt[f"{name}_p95_s"] = histogram_quantile(0.95, d["le"], d["cum"])
                        if name == "e2e":
                            pt["requests_in_interval"] = d["count"]
        pts.append(pt)
        prev = r
    return pts


def downsample(points: list[dict], max_points: int) -> list[dict]:
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    return [points[int(i * step)] for i in range(max_points)]


def accumulate_hist(rows: list[dict], name: str) -> dict | None:
    """Sum per-interval histogram deltas across a run, only inside one counter epoch at a time (resets are skipped, never added)."""
    total: dict | None = None
    prev: dict | None = None
    for r in rows:
        if prev is not None and prev.get("counter_epoch", 0) == r.get("counter_epoch", 0):
            d = hist_delta(prev.get(f"{name}_hist"), r.get(f"{name}_hist"))
            if d:
                if total is None:
                    total = d
                elif total["le"] == d["le"]:
                    total = {"le": d["le"], "cum": [a + b for a, b in zip(total["cum"], d["cum"])], "sum": total["sum"] + d["sum"], "count": total["count"] + d["count"]}
        prev = r
    return total


def counter_total(rows: list[dict], field: str) -> float | None:
    """Total increase of a counter across a run, epoch-aware. None if the counter was never reported."""
    total, seen, prev = 0.0, False, None
    for r in rows:
        v = r.get(field)
        if v is None:
            continue
        if prev is not None and prev[0] == r.get("counter_epoch", 0) and v >= prev[1]:
            total += v - prev[1]
            seen = True
        elif prev is None:
            seen = True
        prev = (r.get("counter_epoch", 0), v)
    return total if seen else None


def run_summary(gpu_rows: list[dict], vllm_rows: list[dict]) -> dict:
    per_gpu: dict[int, dict] = {}
    for r in gpu_rows:
        g = per_gpu.setdefault(r["gpu_index"], {f: [] for f in ("util_gpu_pct", "mem_used_mib", "mem_total_mib", "temp_c", "power_w")})
        for f in g:
            if r.get(f) is not None:
                g[f].append(r[f])
    gpus = {}
    for idx, g in sorted(per_gpu.items()):
        avg = lambda xs: (sum(xs) / len(xs)) if xs else None          # noqa: E731
        gpus[str(idx)] = {"util_gpu_avg_pct": avg(g["util_gpu_pct"]), "util_gpu_max_pct": max(g["util_gpu_pct"], default=None),
                          "mem_used_max_mib": max(g["mem_used_mib"], default=None), "mem_total_mib": max(g["mem_total_mib"], default=None),
                          "temp_max_c": max(g["temp_c"], default=None), "power_avg_w": avg(g["power_w"]), "power_max_w": max(g["power_w"], default=None),
                          "samples": len(g["util_gpu_pct"])}
    out: dict = {"gpus": gpus, "gpu_samples": len(gpu_rows), "vllm_samples": len(vllm_rows)}
    for name in ("ttft", "e2e", "itl"):
        h = accumulate_hist(vllm_rows, name)
        out[f"{name}_p50_s"] = histogram_quantile(0.5, h["le"], h["cum"]) if h and h["count"] > 0 else None
        out[f"{name}_p95_s"] = histogram_quantile(0.95, h["le"], h["cum"]) if h and h["count"] > 0 else None
        out[f"{name}_observations"] = h["count"] if h else None
    out["prompt_tokens"] = counter_total(vllm_rows, "prompt_tokens_total")
    out["generation_tokens"] = counter_total(vllm_rows, "generation_tokens_total")
    out["preemptions"] = counter_total(vllm_rows, "preemptions_total")
    kv = [r["kv_cache_usage_perc"] for r in vllm_rows if r.get("kv_cache_usage_perc") is not None]
    out["kv_cache_max_perc"] = max(kv) * 100 if kv else None
    if len(vllm_rows) >= 2 and out["generation_tokens"] is not None:
        dt = (_ts(vllm_rows[-1]["captured_at"]) - _ts(vllm_rows[0]["captured_at"])).total_seconds()
        out["generation_tokens_per_s_avg"] = out["generation_tokens"] / dt if dt > 0 else None
    else:
        out["generation_tokens_per_s_avg"] = None
    return out
