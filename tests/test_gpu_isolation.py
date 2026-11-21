"""Proves the local-GPU guard works (the guard itself is installed in tests/conftest.py)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from enigma_connector.gpu import NvmlSampler, NvmlUnavailable, local_gpu_forbidden

pytestmark = pytest.mark.skipif(os.environ.get("ENIGMA_REMOTE_GPU_HOST") == "1", reason="guard is deliberately off on the remote GPU host")


def test_env_flag_is_set_and_default_sampler_refuses_before_touching_nvml():
    assert local_gpu_forbidden()
    with pytest.raises(NvmlUnavailable, match="forbidden"):
        NvmlSampler()


@pytest.mark.parametrize("mod", ["pynvml", "torch", "vllm", "pycuda", "cupy", "nvidia.cuda_runtime"])
def test_gpu_libraries_cannot_be_imported(mod):
    with pytest.raises(ImportError, match="blocked"):
        __import__(mod)


def test_nvidia_smi_cannot_be_executed():
    with pytest.raises(PermissionError):
        subprocess.run(["nvidia-smi"], capture_output=True)
    with pytest.raises(PermissionError):
        subprocess.Popen("nvidia-smi -L", shell=True)


def test_ordinary_subprocesses_still_work():
    assert subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True, text=True).stdout.strip() == "1"


def test_connector_cli_refuses_stokastisk_mode_when_forbidden(monkeypatch):
    from enigma_connector import cli
    from enigma_connector.config import Config
    cfg = Config(api_url="http://127.0.0.1:1", token="enig_rt_abcdefgh_" + "A" * 43, mode="stokastisk")
    with pytest.raises(SystemExit, match="refusing"):
        cli.build(cfg)
