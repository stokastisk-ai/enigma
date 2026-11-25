"""Software identity the connector reports at registration, taken ONLY from authoritative local evidence. Nothing is hard-coded.

* stokastisk version: ``stokastisk.__version__`` / the installed distribution's metadata.
* stokastisk build: the ``Build:`` tag in the installed wheel's own ``WHEEL`` file (the ``-5`` in ``stokastisk-0.1.0-5-cp313-...whl``).
* wheel SHA-256: pip's ``direct_url.json`` (PEP 610) records the hash pip VERIFIED when the wheel was installed from a ``...#sha256=`` URL. A plain
  index install records no hash, and then the field is simply omitted: the connector never computes or guesses one.
* vLLM version: the running server's own ``GET /version`` (loopback only) when it answers; otherwise the installed ``vllm`` distribution's metadata.
* python version: ``platform.python_version()``.

Every value is length/format checked against what the API accepts. All of it is SELF-REPORTED by the connector and informational only."""
from __future__ import annotations

import importlib.metadata as md
import json
import platform
import re
import urllib.request
from typing import Any, Callable

_VERSIONISH = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,39}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LOOPBACK = ("127.0.0.1", "localhost", "[::1]", "::1")
MAX_VERSION_BYTES = 4096


def _clean(v: Any) -> str | None:
    return v if isinstance(v, str) and _VERSIONISH.match(v) else None


def build_tag_from_wheel_file(text: str | None) -> str | None:
    """``Build: 5`` from a wheel's ``WHEEL`` metadata file."""
    if not text:
        return None
    for line in text.splitlines():
        if line.lower().startswith("build:"):
            return _clean(line.split(":", 1)[1].strip())
    return None


def sha256_from_direct_url(text: str | None) -> str | None:
    """The sha256 pip verified at install time (PEP 610 ``archive_info``), in either of its two spellings."""
    if not text:
        return None
    try:
        info = (json.loads(text) or {}).get("archive_info") or {}
    except (ValueError, AttributeError):
        return None
    cands = []
    h = info.get("hashes")
    if isinstance(h, dict):
        cands.append(h.get("sha256"))
    legacy = info.get("hash")
    if isinstance(legacy, str) and legacy.startswith("sha256="):
        cands.append(legacy.split("=", 1)[1])
    for c in cands:
        if isinstance(c, str) and _SHA256.match(c.lower()):
            return c.lower()
    return None


def _default_dist() -> Any:
    try:
        return md.distribution("stokastisk")
    except md.PackageNotFoundError:
        return None


def fetch_vllm_version(base_url: str, timeout: float = 3.0) -> str | None:
    """``GET <local vLLM>/version``; loopback only, bounded read, format-checked."""
    from urllib.parse import urlparse
    u = urlparse(base_url)
    if u.hostname not in _LOOPBACK:
        return None
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/version", timeout=timeout) as r:      # noqa: S310 - loopback only (checked above)
            raw = r.read(MAX_VERSION_BYTES + 1)
        if len(raw) > MAX_VERSION_BYTES:
            return None
        return _clean((json.loads(raw) or {}).get("version"))
    except Exception:                                                                              # noqa: BLE001 - metadata is best effort
        return None


def package_vllm_version() -> str | None:
    try:
        return _clean(md.version("vllm"))
    except md.PackageNotFoundError:
        return None


def collect_sdk_identity(vllm_url: str | None = None, *, dist: Any = None, vllm_version_fn: Callable[[str], str | None] = fetch_vllm_version,
                         stokastisk_version: str | None = None, vllm_package_fn: Callable[[], str | None] = package_vllm_version) -> dict[str, str]:
    """Return only the fields that could be established. ``dist`` is injectable for tests (any object with ``version`` and ``read_text``)."""
    d = dist if dist is not None else _default_dist()
    out: dict[str, str | None] = {"python_version": _clean(platform.python_version())}
    ver = _clean(stokastisk_version) or (_clean(getattr(d, "version", None)) if d is not None else None)
    out["stokastisk_version"] = ver
    if d is not None:
        out["stokastisk_build"] = build_tag_from_wheel_file(d.read_text("WHEEL"))
        out["wheel_sha256"] = sha256_from_direct_url(d.read_text("direct_url.json"))
    out["vllm_version"] = (vllm_version_fn(vllm_url) if vllm_url else None) or vllm_package_fn()
    return {k: v for k, v in out.items() if v}
