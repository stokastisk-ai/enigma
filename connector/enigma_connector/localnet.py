"""Loopback HTTP that never goes through a proxy.

``urllib.request.urlopen`` honours ``http_proxy``/``HTTP_PROXY`` and, on Linux, bypasses the proxy only for hosts listed in ``no_proxy``. With a proxy
variable set and no ``no_proxy``, a request to the local vLLM server at 127.0.0.1 was sent to the proxy and failed (reproduced CPU-only). The local
server is never reachable through a proxy, so loopback requests use an opener WITHOUT a proxy handler. Remote requests (the Enigma API) are unchanged:
they still honour the environment's proxy settings and verify TLS as before."""
from __future__ import annotations

import os
import urllib.request
from typing import Any, MutableMapping
from urllib.parse import urlparse

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_loopback(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOOPBACK_HOSTS


def open_loopback(url_or_request: Any, timeout: float):
    """``urlopen`` for loopback URLs only, never through a proxy. Raises ValueError for any other host."""
    url = url_or_request.full_url if isinstance(url_or_request, urllib.request.Request) else str(url_or_request)
    if not is_loopback(url):
        raise ValueError("open_loopback is for 127.0.0.1 / localhost / ::1 only")
    return _DIRECT.open(url_or_request, timeout=timeout)


def ensure_loopback_bypass(environ: MutableMapping[str, str] | None = None) -> None:
    """Add the loopback hosts to ``no_proxy`` and ``NO_PROXY`` (keeping existing entries), so libraries that call plain ``urlopen`` on 127.0.0.1
    (e.g. the Stokastisk client used for streaming) bypass a configured proxy too. Called once in the connector PROCESS (never the notebook kernel)."""
    env = os.environ if environ is None else environ
    for key in ("no_proxy", "NO_PROXY"):
        have = [x.strip() for x in env.get(key, "").split(",") if x.strip()]
        add = [h for h in ("127.0.0.1", "localhost", "::1") if h not in have]
        if add:
            env[key] = ",".join(have + add)
