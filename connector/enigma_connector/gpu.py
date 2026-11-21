"""GPU inventory + sampling with nvidia-ml-py (import name ``pynvml``) and nothing else.

Every reading is optional: an unsupported/unavailable sensor yields ``None`` (never 0, never a guess). A counter that raises
NVML_ERROR_NOT_SUPPORTED once is not polled again (no per-second error spam). NVML init/shutdown are reference-counted by the library,
so this coexists with the SDK's own NVML probes."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("enigma.gpu")
MIB = 1024 * 1024


class NvmlUnavailable(RuntimeError):
    pass


def _s(v: Any) -> str:
    return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)


def local_gpu_forbidden() -> bool:
    """True when this process must never touch a physical GPU (laptop/dev host). Set by tests, smoke scripts and the e2e harness."""
    return os.environ.get("ENIGMA_FORBID_LOCAL_GPU") == "1"


class NvmlSampler:
    def __init__(self, nvml: Any = None):
        if nvml is None:
            if local_gpu_forbidden():            # hard stop BEFORE importing/initialising the real NVML
                raise NvmlUnavailable("local GPU access is forbidden on this host (ENIGMA_FORBID_LOCAL_GPU=1)")
            try:
                import pynvml as nvml            # provided by the nvidia-ml-py distribution
            except ImportError as e:
                raise NvmlUnavailable("nvidia-ml-py (pynvml) is not installed") from e
        self.nv = nvml
        try:
            self.nv.nvmlInit()
        except Exception as e:                   # NVMLError_DriverNotLoaded, LibraryNotFound, NoPermission, ...
            raise NvmlUnavailable(f"NVML could not be initialised: {type(e).__name__}") from e
        self._closed = False
        self._unsupported: set[tuple[int, str]] = set()
        self.count = int(self.nv.nvmlDeviceGetCount())
        self._handles = [self.nv.nvmlDeviceGetHandleByIndex(i) for i in range(self.count)]

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self.nv.nvmlShutdown()
            except Exception:                    # noqa: BLE001  best effort
                log.debug("nvmlShutdown failed", exc_info=True)

    def _try(self, idx: int, field: str, fn):
        if (idx, field) in self._unsupported:
            return None
        try:
            return fn()
        except Exception as e:                   # NVMLError subclasses; also tolerate odd fake/driver failures
            name = type(e).__name__
            if "NotSupported" in name or "FunctionNotFound" in name or "NoPermission" in name:
                self._unsupported.add((idx, field))
            else:
                log.debug("nvml %s failed on gpu %d: %s", field, idx, name)
            return None

    def inventory(self) -> list[dict]:
        nv = self.nv
        out = []
        driver = self._try(-1, "driver", lambda: _s(nv.nvmlSystemGetDriverVersion()))
        for i, h in enumerate(self._handles):
            mem = self._try(i, "mem_inv", lambda h=h: nv.nvmlDeviceGetMemoryInfo(h))
            out.append({"index": i, "uuid": self._try(i, "uuid", lambda h=h: _s(nv.nvmlDeviceGetUUID(h))),
                        "name": self._try(i, "name", lambda h=h: _s(nv.nvmlDeviceGetName(h))),
                        "memory_total_mib": (mem.total / MIB) if mem is not None else None, "driver": driver})
        return out

    def sample(self, now: datetime | None = None) -> list[dict]:
        nv = self.nv
        ts = (now or datetime.now(timezone.utc)).isoformat()
        rows = []
        for i, h in enumerate(self._handles):
            util = self._try(i, "util", lambda h=h: nv.nvmlDeviceGetUtilizationRates(h))
            mem = self._try(i, "mem", lambda h=h: nv.nvmlDeviceGetMemoryInfo(h))
            temp = self._try(i, "temp", lambda h=h: nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU))
            power = self._try(i, "power", lambda h=h: nv.nvmlDeviceGetPowerUsage(h))
            limit = self._try(i, "power_limit", lambda h=h: nv.nvmlDeviceGetEnforcedPowerLimit(h))
            sm = self._try(i, "sm_clock", lambda h=h: nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_SM))
            mc = self._try(i, "mem_clock", lambda h=h: nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_MEM))
            rx = self._try(i, "pcie_rx", lambda h=h: nv.nvmlDeviceGetPcieThroughput(h, nv.NVML_PCIE_UTIL_RX_BYTES))
            tx = self._try(i, "pcie_tx", lambda h=h: nv.nvmlDeviceGetPcieThroughput(h, nv.NVML_PCIE_UTIL_TX_BYTES))
            rows.append({
                "captured_at": ts, "gpu_index": i, "gpu_uuid": self._try(i, "uuid", lambda h=h: _s(nv.nvmlDeviceGetUUID(h))),
                "util_gpu_pct": float(util.gpu) if util is not None else None,
                "util_mem_pct": float(util.memory) if util is not None else None,          # memory-controller busy %, not capacity
                "mem_used_mib": mem.used / MIB if mem is not None else None,
                "mem_total_mib": mem.total / MIB if mem is not None else None,
                "temp_c": float(temp) if temp is not None else None,
                "power_w": power / 1000.0 if power is not None else None,                  # NVML reports milliwatts
                "power_limit_w": limit / 1000.0 if limit is not None else None,
                "sm_clock_mhz": float(sm) if sm is not None else None,
                "mem_clock_mhz": float(mc) if mc is not None else None,
                "pcie_rx_kib_s": float(rx) if rx is not None else None,                    # NVML: KB/s
                "pcie_tx_kib_s": float(tx) if tx is not None else None,
            })
        return rows

    def supported_counters(self) -> dict[str, bool]:
        """Which optional counters this host exposes, derived from what has actually failed so far (call after a few samples)."""
        names = ("util", "mem", "temp", "power", "power_limit", "sm_clock", "mem_clock", "pcie_rx", "pcie_tx")
        return {n: not all((i, n) in self._unsupported for i in range(self.count)) for n in names} if self.count else {}
