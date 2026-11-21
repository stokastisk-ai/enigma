"""Best-effort per-key token bucket. Serverless instances do not share memory, so this only smooths bursts inside one warm instance.
The durable caps (active jobs, events per job, rows per batch, body size) live in SQL / request validation, not here."""
from __future__ import annotations

import threading
import time
from typing import Callable

from .errors import ApiError


class RateLimiter:
    def __init__(self, rate_per_s: float, burst: int, clock: Callable[[], float] = time.monotonic, max_keys: int = 10_000):
        self.rate, self.burst, self.clock, self.max_keys = rate_per_s, burst, clock, max_keys
        self._b: dict[str, tuple[float, float]] = {}
        self._l = threading.Lock()

    def check(self, key: str) -> None:
        now = self.clock()
        with self._l:
            tokens, last = self._b.get(key, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < 1:
                retry = max(1, int((1 - tokens) / self.rate) + 1)
                raise ApiError(429, "rate_limited", "too many requests", headers={"retry-after": str(retry)})
            if len(self._b) >= self.max_keys:
                self._b.clear()                      # bounded memory; at worst a window of leniency
            self._b[key] = (tokens - 1, now)
