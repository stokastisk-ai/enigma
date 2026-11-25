"""Tiny HTTPS client on urllib (no extra pip packages on the GPU host). Retries with exponential backoff + jitter."""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .config import Config


MAX_RESPONSE_BYTES = 1024 * 1024        # every Enigma API reply is a few KiB; refuse to buffer more than this


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "", extra: dict | None = None):
        super().__init__(f"{status} {code}")
        self.status, self.code, self.message, self.extra = status, code, message, extra or {}


class AuthError(ApiError):
    """401/403: the token was revoked, rotated or expired. Fatal: retrying cannot help."""


class Transient(Exception):
    """Network failure, 5xx or 429. Safe to retry (every call is idempotent by design)."""


class ApiClient:
    def __init__(self, cfg: Config, opener: Callable | None = None):
        self.cfg = cfg
        self._open = opener or urllib.request.urlopen

    def request(self, method: str, path: str, body: Any = None) -> dict:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        req = urllib.request.Request(self.cfg.api_url + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.cfg.token}", "Content-Type": "application/json", "X-Enigma-Instance": self.cfg.instance_id,
            "User-Agent": "enigma-connector/0.1"})
        try:
            with self._open(req, timeout=self.cfg.request_timeout_s) as r:
                raw = r.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise Transient("response_too_large")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            payload = {}
            try:
                payload = json.loads(e.read(MAX_RESPONSE_BYTES).decode("utf-8", "replace")).get("error", {})
            except (ValueError, AttributeError):
                pass
            code = payload.get("code", "http_error")
            if e.code in (401, 403):
                raise AuthError(e.code, code, payload.get("message", "")) from None
            if e.code == 429 or e.code >= 500:
                raise Transient(f"{e.code} {code}") from None
            raise ApiError(e.code, code, payload.get("message", ""), {k: v for k, v in payload.items() if k not in ("code", "message", "correlation_id")}) from None
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            raise Transient(type(e).__name__) from None

    def post(self, path: str, body: Any = None) -> dict:
        return self.request("POST", path, body if body is not None else {})

    def get(self, path: str) -> dict:
        return self.request("GET", path)


def backoff_delays(base: float = 0.5, cap: float = 30.0, rng: random.Random | None = None):
    """Exponential backoff with full jitter: yields delays forever."""
    r = rng or random.Random()
    n = 0
    while True:
        yield r.uniform(0, min(cap, base * (2 ** n)))
        n += 1


def retry(fn: Callable[[], Any], attempts: int = 5, sleep: Callable[[float], None] = time.sleep, base: float = 0.5, cap: float = 30.0,
          rng: random.Random | None = None):
    """Retry only Transient errors. AuthError / ApiError propagate immediately."""
    delays = backoff_delays(base, cap, rng)
    for i in range(attempts):
        try:
            return fn()
        except Transient:
            if i == attempts - 1:
                raise
            sleep(next(delays))
