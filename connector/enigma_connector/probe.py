"""Offline probes (diagnostic stages ``nvml`` and ``vllm``): exercise ONE native/local component repeatedly with NO network traffic to Enigma and no token.

They exist to answer "does this component, on its own, survive repeated use on this host?" before anything is uploaded. Every iteration writes a
flushed + fsynced line to the diagnostic log, so after a hard crash the last line names the last call that completed."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .diag import DiagLog
from .vllm_metrics import VllmSampler


def _loop(stop: threading.Event, interval_s: float, duration_s: float, body: Callable[[int], None], clock: Callable[[], float] = time.monotonic) -> int:
    t_end = clock() + duration_s if duration_s > 0 else None
    i = 0
    while not stop.is_set() and (t_end is None or clock() < t_end):
        t0 = clock()
        i += 1
        body(i)
        stop.wait(max(0.0, interval_s - (clock() - t0)))
    return i


def run_local_nvml(sampler: Any, diag: DiagLog, stop: threading.Event, *, interval_s: float, duration_s: float) -> dict:
    """Repeated NVML inventory + sampling from a BACKGROUND thread (the way the connector uses it), never uploaded."""
    inv = sampler.inventory()
    diag.emit("nvml_inventory", gpus=len(inv), names=[g.get("name") for g in inv], memory_total_mib=[g.get("memory_total_mib") for g in inv])
    result: dict[str, Any] = {"samples": 0, "errors": 0}

    def body(i: int) -> None:
        t0 = time.monotonic()
        try:
            rows = sampler.sample()
        except Exception as e:                                                      # noqa: BLE001 - recorded, then the probe carries on
            result["errors"] += 1
            diag.emit("nvml_sample_error", i=i, error=type(e).__name__)
            return
        result["samples"] += 1
        diag.emit("nvml_sample", i=i, ms=round((time.monotonic() - t0) * 1000, 1), mem_used_mib=[r.get("mem_used_mib") for r in rows],
                  temp_c=[r.get("temp_c") for r in rows], power_w=[r.get("power_w") for r in rows], util_gpu_pct=[r.get("util_gpu_pct") for r in rows])

    worker = threading.Thread(target=lambda: _loop(stop, interval_s, duration_s, body), name="enigma-probe-nvml", daemon=True)
    worker.start()
    worker.join()
    result["unsupported_counters"] = sorted(f"gpu{i}:{f}" for i, f in getattr(sampler, "_unsupported", set()))
    diag.emit("nvml_probe_done", **result)
    return result


def run_local_vllm(sampler: VllmSampler, diag: DiagLog, stop: threading.Event, *, interval_s: float, duration_s: float) -> dict:
    """Repeated scrape + parse of the LOCAL vLLM /metrics, never uploaded."""
    result: dict[str, Any] = {"scrapes": 0, "empty": 0}

    def body(i: int) -> None:
        t0 = time.monotonic()
        s = sampler.sample()
        result["scrapes"] += 1
        if s is None:
            result["empty"] += 1
        diag.emit("vllm_scrape", i=i, got=s is not None, ms=round((time.monotonic() - t0) * 1000, 1), error=sampler.last_error,
                  generation_tokens_total=(s or {}).get("generation_tokens_total"), epoch=(s or {}).get("counter_epoch"))

    worker = threading.Thread(target=lambda: _loop(stop, interval_s, duration_s, body), name="enigma-probe-vllm", daemon=True)
    worker.start()
    worker.join()
    diag.emit("vllm_probe_done", **result)
    return result
