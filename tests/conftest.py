"""Hard local-GPU isolation for the WHOLE test session (skills.md section 15.1).

This laptop's NVIDIA GPU must never be used, probed or initialised. Layers, all active before any test module imports:
  1. ENIGMA_FORBID_LOCAL_GPU=1 in the environment (inherited by every child process: uvicorn, connector, smoke script).
  2. An import blocker: pynvml / torch / vllm / CUDA libraries cannot be imported (ImportError), so NvmlSampler() can never reach a driver.
  3. A subprocess blocker: any attempt to execute nvidia-smi / nvidia-* tools raises.
Tests that need NVML pass an explicit fake object (see tests/connector/test_metrics_and_gpu.py). Real-GPU tests are REMOTE-ONLY
(ENIGMA_REMOTE_GPU_HOST=1, Kaggle) and are skipped here.
"""
from __future__ import annotations

import importlib.abc
import os
import subprocess
import sys

BLOCKED_ROOTS = ("pynvml", "nvidia_ml_py", "torch", "vllm", "pycuda", "cupy", "tensorflow", "nvidia", "triton")
REMOTE = os.environ.get("ENIGMA_REMOTE_GPU_HOST") == "1"


class LocalGpuForbidden(ImportError):
    pass


class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED_ROOTS:
            raise LocalGpuForbidden(f"import of {fullname!r} is blocked: local GPU libraries are forbidden in tests")
        return None


def _install() -> None:
    if REMOTE:
        return
    os.environ["ENIGMA_FORBID_LOCAL_GPU"] = "1"
    for name in [m for m in sys.modules if m.split(".")[0] in BLOCKED_ROOTS]:
        del sys.modules[name]                    # drop anything imported earlier so it cannot be reused
    if not any(isinstance(f, _Blocker) for f in sys.meta_path):
        sys.meta_path.insert(0, _Blocker())
    orig = subprocess.Popen.__init__

    def guarded(self, args, *a, **kw):
        argv = args if isinstance(args, (list, tuple)) else str(args).split()
        if argv and os.path.basename(str(argv[0])).startswith(("nvidia-", "nvidia_")) or "nvidia-smi" in " ".join(map(str, argv)):
            raise PermissionError("executing nvidia-smi/NVIDIA tools is forbidden on this host")
        return orig(self, args, *a, **kw)

    subprocess.Popen.__init__ = guarded


_install()

# ---- network guard: tests may talk to loopback / unix sockets only (moto and the fake IDP/servers need nothing else).
# This also means no test can ever reach a real AWS endpoint, whatever credentials or mocks are (mis)configured.
import socket  # noqa: E402

_LOOPBACK = ("127.", "::1", "localhost", "0.0.0.0", "::")


class NetworkForbidden(OSError):
    pass


def _is_local(addr) -> bool:
    if isinstance(addr, (str, bytes)):          # AF_UNIX path
        return True
    host = str(addr[0]) if addr else ""
    return host == "" or host.startswith(_LOOPBACK)


def _install_network_guard() -> None:
    if os.environ.get("ENIGMA_ALLOW_NETWORK") == "1":
        return
    real_connect, real_connect_ex, real_gai = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def connect(self, addr):
        if not _is_local(addr):
            raise NetworkForbidden(f"tests may not open non-loopback connections (to {addr[0]!r})")
        return real_connect(self, addr)

    def connect_ex(self, addr):
        if not _is_local(addr):
            raise NetworkForbidden(f"tests may not open non-loopback connections (to {addr[0]!r})")
        return real_connect_ex(self, addr)

    def getaddrinfo(host, *a, **kw):
        if host not in (None, "", "localhost") and not str(host).startswith(_LOOPBACK) and not str(host).startswith("/"):
            raise NetworkForbidden(f"tests may not resolve external host {host!r}")
        return real_gai(host, *a, **kw)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, getaddrinfo


_install_network_guard()
