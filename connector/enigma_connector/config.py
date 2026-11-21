from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_TOKEN = re.compile(r"^enig_rt_[a-z0-9]{8}_[A-Za-z0-9_-]{43}$")


class ConfigError(ValueError):
    pass


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
    claim_poll_s: float = 1.0
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
        if e.get("ENIGMA_HEARTBEAT_S"):
            cfg.heartbeat_interval_s = float(e["ENIGMA_HEARTBEAT_S"])           # keep it well under the server's online threshold (default 30 s)
        for k, v in over.items():
            setattr(cfg, k, v)
        return cfg.validate()
