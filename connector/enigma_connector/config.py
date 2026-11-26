from __future__ import annotations

import math
import os
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_TOKEN = re.compile(r"^enig_rt_[a-z0-9]{8}_[A-Za-z0-9_-]{43}$")


class ConfigError(ValueError):
    pass


def finite_number(name: str, value: object, *, minimum: float = 0.0, maximum: float = 3600.0, allow_minimum: bool = False) -> float:
    """``value`` as a float that is finite and within (minimum, maximum] (or [minimum, maximum] with ``allow_minimum``). Rejects NaN, +/-inf and bools."""
    if isinstance(value, bool):
        raise ConfigError(f"{name} must be a number, got {value!r}")
    try:
        v = float(value)                                                                   # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a number, got {value!r}") from None
    low_ok = v >= minimum if allow_minimum else v > minimum
    if not math.isfinite(v) or not low_ok or v > maximum:
        raise ConfigError(f"{name} must be a finite number in {'[' if allow_minimum else '('}{minimum:g}, {maximum:g}], got {value!r}")
    return v


# (attribute, minimum, maximum, minimum allowed). Every cadence and timeout the connector sleeps or waits on.
INTERVAL_LIMITS: tuple[tuple[str, float, float, bool], ...] = (
    ("gpu_interval_s", 0.0, 3600.0, False), ("vllm_interval_s", 0.0, 3600.0, False), ("upload_interval_s", 0.0, 3600.0, False),
    ("heartbeat_interval_s", 0.0, 3600.0, False), ("claim_poll_s", 0.01, 60.0, True), ("claim_poll_idle_s", 0.0, 60.0, True),
    ("claim_hot_window_s", 0.0, 3600.0, True), ("flush_interval_s", 0.0, 60.0, False), ("keepalive_s", 0.0, 3600.0, False),
    ("request_timeout_s", 0.0, 600.0, False), ("stream_read_timeout_s", 0.0, 3600.0, False),
)


@dataclass
class Config:
    api_url: str
    token: str = field(repr=False)                       # never shown in repr/logs
    instance_id: str = ""
    vllm_url: str = "http://127.0.0.1:8000"
    served_model: str = ""
    mode: str = "stokastisk"                             # stokastisk | mock
    tp: int = 1
    pp: int = 1
    max_model_len: int | None = 2048
    # cadence / limits (skills.md: 1 s GPU sampling, 5 s upload)
    gpu_interval_s: float = 1.0
    vllm_interval_s: float = 2.0
    upload_interval_s: float = 5.0
    heartbeat_interval_s: float = 10.0
    claim_poll_s: float = 1.0                            # fast poll (also the idle poll unless claim_poll_idle_s is larger)
    claim_poll_idle_s: float = 5.0                       # conservative idle ceiling (default since 2026-11-22); 0 = same as claim_poll_s (no back-off). e.g. 5.0 cuts claim polls ~72 % (API invocations ~35 %) at ~5 s worst-case pickup; saves < USD 0.4/mo
    claim_hot_window_s: float = 30.0                     # poll fast this long after start / after any job
    flush_interval_s: float = 0.15                       # token batching window for the browser stream
    keepalive_s: float = 5.0                             # lease renewal while the model is still prefilling
    lease_seconds: int = 30
    request_timeout_s: float = 15.0
    stream_read_timeout_s: float = 60.0
    spool_dir: str = ""
    spool_max_bytes: int = 20 * 1024 * 1024
    max_batch_gpu_rows: int = 600
    max_batch_vllm_rows: int = 120

    def validate(self) -> "Config":
        u = urlparse(self.api_url)
        if u.scheme not in ("https", "http") or not u.hostname:
            raise ConfigError("ENIGMA_API_URL must be an http(s) URL")
        if u.scheme == "http" and u.hostname not in _LOCAL_HOSTS:
            raise ConfigError("plain http is only allowed for localhost; use https for a remote Enigma API")
        if not _TOKEN.match(self.token or ""):
            raise ConfigError("ENIGMA_RUNTIME_TOKEN is missing or malformed (expected enig_rt_<8>_<43>)")
        for attr, lo, hi, lo_ok in INTERVAL_LIMITS:                                     # finite, positive (or explicitly allowed zero), bounded
            setattr(self, attr, finite_number(attr, getattr(self, attr), minimum=lo, maximum=hi, allow_minimum=lo_ok))
        if self.mode not in ("stokastisk", "mock"):
            raise ConfigError("mode must be 'stokastisk' or 'mock'")
        lv = urlparse(self.vllm_url)
        if lv.hostname not in _LOCAL_HOSTS:
            raise ConfigError("the vLLM server must be on localhost: it is never exposed or tunnelled")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,48}", self.instance_id):
            raise ConfigError("instance_id must match [A-Za-z0-9._-]{1,48}")
        return self

    @staticmethod
    def from_env(env: dict | None = None, **over) -> "Config":
        e = env if env is not None else os.environ
        cfg = Config(api_url=e.get("ENIGMA_API_URL", "").rstrip("/"), token=e.get("ENIGMA_RUNTIME_TOKEN", ""),
                     instance_id=e.get("ENIGMA_INSTANCE", f"{socket.gethostname()[:30]}-{os.getpid()}".replace(" ", "-")),
                     vllm_url=e.get("ENIGMA_VLLM_URL", "http://127.0.0.1:8000"), served_model=e.get("ENIGMA_SERVED_MODEL", ""),
                     mode=e.get("ENIGMA_MODE", "stokastisk"), spool_dir=e.get("ENIGMA_SPOOL_DIR", ""))
        for env_key, attr in (("ENIGMA_HEARTBEAT_S", "heartbeat_interval_s"),            # keep heartbeats well under the server's online threshold (default 30 s)
                              ("ENIGMA_CLAIM_POLL_S", "claim_poll_s"), ("ENIGMA_CLAIM_POLL_IDLE_S", "claim_poll_idle_s"), ("ENIGMA_CLAIM_HOT_WINDOW_S", "claim_hot_window_s")):
            if e.get(env_key):
                try:
                    setattr(cfg, attr, float(e[env_key]))
                except ValueError:
                    raise ConfigError(f"{env_key} must be a number") from None
        for k, v in over.items():
            setattr(cfg, k, v)
        return cfg.validate()
