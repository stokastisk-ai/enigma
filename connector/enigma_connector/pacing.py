"""Claim-poll pacing: poll fast while work is likely, back off while idle.

Cost/latency trade-off (docs/AWS_COST_MATRIX.md): at the default 1 s poll the connector is the largest consumer of API invocations. With
``claim_poll_idle_s`` > ``claim_poll_s`` the interval ramps geometrically from the fast rate up to the idle ceiling once nothing has happened for
``claim_hot_window_s``; any job (or the connector's start) re-opens the hot window. Defaults keep today's behaviour exactly (idle == fast).
Worst-case pickup latency of a job submitted while idle is about the idle ceiling (plus jitter); measure it before publishing a latency claim.
"""
from __future__ import annotations

import random
import time
from typing import Callable


class ClaimPacer:
    def __init__(self, fast_s: float, idle_s: float = 0.0, hot_window_s: float = 30.0, *, growth: float = 1.5, jitter: float = 0.1,
                 clock: Callable[[], float] = time.monotonic, rand: Callable[[], float] = random.random):
        self.fast = max(0.01, float(fast_s))
        self.idle = max(self.fast, float(idle_s) if idle_s else self.fast)          # 0 / unset => idle == fast => no back-off
        self.hot_window, self.growth, self.jitter = max(0.0, float(hot_window_s)), max(1.0, float(growth)), max(0.0, min(0.5, float(jitter)))
        if self.idle == self.fast:
            self.jitter = 0.0                                                         # no back-off configured: behave exactly like a fixed-rate poll
        self._clock, self._rand = clock, rand
        self._cur = self.fast
        self._last_activity = clock()                                                 # starting up counts as activity

    def on_activity(self) -> None:
        """A job was claimed/finished: poll fast again for the hot window."""
        self._last_activity = self._clock()
        self._cur = self.fast

    def next_wait(self, server_hint_s: float = 0.0) -> float:
        """Seconds to wait after an EMPTY claim. The server's hint is only ever a floor."""
        if self._clock() - self._last_activity < self.hot_window:
            self._cur = self.fast
        else:
            self._cur = min(self.idle, max(self.fast, self._cur * self.growth))
        w = self._cur * (1.0 + self.jitter * (2.0 * self._rand() - 1.0))
        return max(w, server_hint_s)
