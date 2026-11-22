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


def test_api_url_is_a_placeholder_not_a_real_host():
    assert "https://YOUR-ENIGMA-API-HOST" in CODE and "amazonaws.com" not in CODE


def test_tp1_is_default_and_tp2_is_a_separate_labelled_unverified_experiment():
    assert "tensor_parallel_size=1" in CODE and "tensor_parallel_size=2" in CODE
    assert "SEPARATE EXPERIMENT: TP=2" in ALL and "UNVERIFIED" in ALL
    after = "\n".join("".join(c["source"]) for c in NB["cells"][NB["cells"].index(next(c for c in NB["cells"] if "SEPARATE EXPERIMENT: TP=2" in "".join(c["source"]))):])
    assert "BOTH GPUs" in after and "tp2_evidence" in after


def test_no_hosted_model_or_aws_gpu_path():
    assert not re.search(r"bedrock|sagemaker|openai\.com|anthropic\.com|boto3", CODE, re.I)
