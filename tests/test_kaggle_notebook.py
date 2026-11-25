"""Static governance of docs/kaggle/enigma_connector_kaggle.ipynb (it is never executed here: it is Kaggle-only)."""
from __future__ import annotations

import json
import re
from pathlib import Path

NB = json.loads((Path(__file__).resolve().parents[1] / "docs/kaggle/enigma_connector_kaggle.ipynb").read_text())
CODE = "\n".join("".join(c["source"]) for c in NB["cells"] if c["cell_type"] == "code")
ALL = "\n".join("".join(c["source"]) for c in NB["cells"])


def test_guards_python313_remote_only_and_exact_wheel_hash():
    assert "/kaggle/working" in CODE and "ENIGMA_FORBID_LOCAL_GPU" in CODE
    assert "sys.version_info[:2] == (3, 13)" in CODE
    assert "cp313-cp313-linux_x86_64.whl#sha256=fe80d7c36fa78cf8a26feb1e00bb5f1f006ca45a027450353603976320ae0b3e" in CODE
    assert "--index-url https://pypi.org/simple" in CODE


def test_token_comes_from_kaggle_secrets_and_is_never_printed_or_hardcoded():
    assert 'UserSecretsClient().get_secret("ENIGMA_RUNTIME_TOKEN")' in CODE
    assert not re.search(r"enig_rt_[a-z0-9]{8}_", ALL)
    assert not re.search(r"print\([^)]*ENIGMA_RUNTIME_TOKEN", CODE)


def test_api_url_is_the_public_railway_https_endpoint_with_no_credentials():
    import re as _re
    m = _re.search(r'ENIGMA_API_URL"\] = "(https://[^"]+)"', CODE)
    assert m, "the notebook must set an https ENIGMA_API_URL"
    assert m.group(1).endswith(".up.railway.app") and "@" not in m.group(1) and "?" not in m.group(1)
    assert "amazonaws.com" not in CODE and "vercel" not in CODE.lower()


def test_preflight_waits_for_the_sleeping_api_with_bounded_read_only_retries():
    assert "/api/v1/health" in CODE and "range(1, 7)" in CODE and "urlopen" in CODE
    assert "Never retries anything that writes" in CODE


def test_tp1_is_default_and_tp2_is_a_separate_labelled_unverified_experiment():
    assert "tensor_parallel_size=1" in CODE and "tensor_parallel_size=2" in CODE
    assert "SEPARATE EXPERIMENT: TP=2" in ALL and "UNVERIFIED" in ALL
    after = "\n".join("".join(c["source"]) for c in NB["cells"][NB["cells"].index(next(c for c in NB["cells"] if "SEPARATE EXPERIMENT: TP=2" in "".join(c["source"]))):])
    assert "BOTH GPUs" in after and "tp2_evidence" in after


def test_no_hosted_model_or_aws_gpu_path():
    assert not re.search(r"bedrock|sagemaker|openai\.com|anthropic\.com|boto3", CODE, re.I)


# ---- staged diagnostic cells (docs/kaggle/enigma_connector_diagnostic_cells.md): they are copy-pasted into a notebook, so they must at least parse and stay in sync


def _cells_text() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "docs" / "kaggle" / "enigma_connector_diagnostic_cells.md").read_text()


def test_diagnostic_cells_all_compile_and_reference_only_real_stages_and_helpers():
    import ast
    from enigma_connector import stage_runner
    from enigma_connector.cli import OFFLINE_STAGES, STAGES
    text = _cells_text()
    blocks = re.findall(r"```python\n(.*?)```", text, flags=re.S)
    assert len(blocks) >= 10
    for b in blocks:
        ast.parse(b)                                                           # a typo here would be found on a Kaggle GPU session otherwise
    stages = set(re.findall(r'stage\("([a-z-]+)"', text))
    assert stages and stages <= {*STAGES, *OFFLINE_STAGES}, stages - {*STAGES, *OFFLINE_STAGES}
    assert {"nvml", "vllm", "register", "heartbeat", "jobs", "upload-once", "telemetry", "full"} <= stages
    assert hasattr(stage_runner, "run_stage")


def test_diagnostic_cells_contain_no_credentials_and_pass_the_key_only_through_a_getter():
    text = _cells_text()
    assert not re.search(r"enig_rt_[a-z0-9]{8}_[A-Za-z0-9_-]{10,}", text)
    assert 'UserSecretsClient().get_secret("ENIGMA_RUNTIME_TOKEN")' in text and "token_getter" in text
    assert not re.search(r"print\([^)]*(token|ENIGMA_RUNTIME_TOKEN)", text, re.I)
    assert "--token" not in text, "the key must never be a command-line argument"


def test_diagnostic_cells_pin_a_revision_that_contains_the_stage_runner():
    import subprocess
    from pathlib import Path
    rev = re.search(r'REV = "([0-9a-f]{7,40})"', _cells_text()).group(1)
    root = Path(__file__).resolve().parents[1]
    if subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{rev}^{{commit}}"], capture_output=True).returncode != 0:
        import pytest
        pytest.skip(f"revision {rev} is not in this clone's history (shallow CI checkout); verified on full clones")
    r = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{rev}:connector/enigma_connector/stage_runner.py"], capture_output=True)
    assert r.returncode == 0, f"revision {rev} does not contain connector/enigma_connector/stage_runner.py (push it, or update REV)"
