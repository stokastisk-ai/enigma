"""The three generated Kaggle notebooks (docs/kaggle/diagnostics/): build 5 only, independent, honest. CPU-only checks; nothing here runs a GPU step."""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "docs" / "kaggle" / "diagnostics"
spec = importlib.util.spec_from_file_location("nbgen", ROOT / "scripts" / "build_kaggle_diagnostic_notebooks.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)
N1, N2, N3 = "01_enigma_kaggle_platform_aws_diagnostics", "02_stokastisk_build5_kaggle_t4_validation", "03_enigma_stokastisk_build5_aws_integration"


def code(name):
    return ["".join(c["source"]) for c in json.loads((DIR / f"{name}.ipynb").read_text())["cells"] if c["cell_type"] == "code"]


def test_exactly_the_three_notebooks_exist_and_equal_the_generator():
    assert sorted(p.stem for p in DIR.glob("*.ipynb")) == sorted([N1, N2, N3]) == sorted(gen.NOTEBOOKS)
    for name in gen.NOTEBOOKS:
        assert (DIR / f"{name}.ipynb").read_text() == gen.render(name), "regenerate: python3.13 scripts/build_kaggle_diagnostic_notebooks.py"
        nb = json.loads((DIR / f"{name}.ipynb").read_text())
        assert all(c.get("outputs", []) == [] and c.get("execution_count") is None for c in nb["cells"] if c["cell_type"] == "code")
        for src in code(name):
            ast.parse(src)


@pytest.mark.parametrize("name", [N1, N2, N3])
def test_no_build6_anywhere_and_each_has_a_guarded_prelude(name):
    text = "\n".join(code(name))
    assert not re.search(r"build[-_ ]?6|candidate|1b3b2572", text, re.I)
    first = code(name)[0]
    assert f'NOTEBOOK = "{name}"' in first and f'EXPECT = "{gen.NOTEBOOKS[name]}"' in first and 'os.makedirs(ATTEMPT_DIR, exist_ok=False)' in first


@pytest.mark.parametrize("name", [N2, N3])
def test_gpu_notebooks_verify_the_exact_published_build5_bytes_before_installing(name):
    text = "\n".join(code(name))
    assert gen.WHEEL_SHA256 in text and f"WHEEL_SIZE = {gen.WHEEL_SIZE}" in text and gen.WHEEL in text
    assert text.index("if n != WHEEL_SIZE or got != WHEEL_SHA256") < text.index('"pip", "install", "--dry-run"') < text.index('"pip", "install", "--index-url"')
    assert "ACKNOWLEDGE_PYDANTIC_SETTINGS_DOWNGRADE = False" in text and '_build == "5"' in text
    assert text.index('os.environ[_k] = ",".join') < text.index("server.start_and_wait")          # loopback no_proxy before the build-5 server starts
    assert "--no-deps" not in text
    assert "pre_rc, pre_conflicts = pip_check()" in text and "and not new_conflicts)" in text                   # install PASS requires no NEW conflict


def test_notebook_01_is_stdlib_only_and_orders_idle_controls_aws_railway():
    text = "\n".join(code(N1))
    assert "REQUIRE_NO_INPUTS = True" in code(N1)[0]
    assert not re.search(r"^\s*(import|from)\s+(torch|pynvml|vllm|stokastisk|triton|enigma_connector)\b", text, re.M)
    assert '"-m", "pip"' not in text and '"git",' not in text                                       # no installs, no clone
    assert 'get_credential("ENIGMA_RUNTIME_TOKEN")' not in text and "token_getter" not in text and not re.search(r"^\s*(import|from)\s+boto3", text, re.M) and "AWS_SECRET" not in text
    order = [text.index(k) for k in ('observe("idle_10min", 600', 'get("pypi"', 'observe("controls_survival_120s"', 'check_credential("ENIGMA_DIAG_KEY"',
                                     'rmark("aws_first_event"', "dup = resubmit_last()", 'observe("aws_survival_120s"', 'rcheck("railway_marker", "before_railway"',
                                     "/api/v1/health", 'rmark("after_railway"', 'observe("railway_survival_120s"', 'verdict("overall", "PASS"')]
    assert order == sorted(order)
    # no secret is read before (or during) the offline idle baseline: the shared cell only DEFINES functions
    cells = code(N1)
    idle = next(i for i, c in enumerate(cells) if 'observe("idle_10min"' in c)
    assert not any(re.search(r"^(check_credential|get_credential|rmark|rcheck)\(", c, re.M) for c in cells[:idle + 1])
    # the credential preflight cell is re-runnable on its own: it refreshes only the credential cache
    pre = next(c for c in cells if 'check_credential("ENIGMA_DIAG_KEY"' in c)
    assert 'refresh_credentials("ENIGMA_DIAG_KEY")' in pre and "observe(" not in pre and "ATTEMPT =" not in pre


def test_notebook_02_never_contacts_enigma():
    text = "\n".join(code(N2))
    assert "railway.app" not in text and 'get_credential("ENIGMA_RUNTIME_TOKEN")' not in text and "enigma_connector" not in text
    assert 'VALIDATION_MODE = "standalone"' in code(N2)[0]                                           # Mode A by default: no secret needed
    mode = next(c for c in code(N2) if "REMOTE_ENABLED = VALIDATION_MODE ==" in c)
    assert mode.index("if REMOTE_ENABLED:") < mode.index('check_credential("ENIGMA_DIAG_KEY"') < mode.index('verdict("remote_ready", "PASS", f"Mode B')
    assert text.index('verdict("remote_ready"') < text.index('require("remote_ready")') < text.index("urllib.request.urlopen(WHEEL_URL")   # Mode B: AWS before GPU work
    assert '"mode": VALIDATION_MODE' in code(N2)[0]
    assert "server.stop()" in text and "p.poll() is None" in text                                  # build-5 stop() only while the leader is unreaped


def test_notebook_03_is_gated_supervised_ordered_and_requires_a_real_job():
    text = "\n".join(code(N3))
    assert "BASELINES_PASSED = False" in text and "ALLOW_CLOUD_CONNECTION = False" in text
    assert [m.group(1) for m in re.finditer(r'stage\("([a-z-]+)"', text)] == ["nvml", "vllm", "register", "heartbeat", "jobs", "upload-once", "telemetry", "full"]
    assert '(stats.get("jobs_done") or 0) >= 1' in text and "run_stage(" in text and "serverctl.safe_stop(server)" in text
    assert '"--gpu-interval", "1.0", "--vllm-interval", "2.0", "--upload-interval", "5.0"' in text
    assert gen.ENIGMA_REV in text
    # both credentials validated independently, durable AWS ack, then EVIDENCE of the cutover (not a boolean), all before the wheel download
    order = [text.index(k) for k in ('check_credential("ENIGMA_DIAG_KEY", "diag_key_preflight")', 'check_credential("ENIGMA_RUNTIME_TOKEN", "runtime_token_preflight")',
                                     "dup = resubmit_last()", 'verdict("production_migration"', "urllib.request.urlopen(WHEEL_URL")]
    assert order == sorted(order)
    assert 'store == "dynamodb" and auth == "cognito"' in text and "MIGRATION_COMPLETE" not in text
    assert 'rcheck(f"stage_{name}_marker"' in text                                                  # a failed before-stage marker stops the stage
    assert 'get_credential("ENIGMA_RUNTIME_TOKEN")' in text


@pytest.mark.parametrize("name", [N1, N2, N3])
def test_no_notebook_embeds_or_prints_a_credential(name):
    text = "\n".join(code(name))
    assert not re.search(r"(enig_rt|edx)_[a-z0-9]{8}_[A-Za-z0-9_-]{10,}", text)
    assert not re.search(r"AKIA[0-9A-Z]{16}|aws_secret_access_key", text, re.I)
    assert not re.search(r"print\([^)]*(token|get_secret|key\b)", text, re.I)
    assert "dict(os.environ)" not in text and "postgres://" not in text and "postgresql://" not in text


def test_remote_markers_send_only_whitelisted_fields_and_ack_only_after_a_durable_write():
    shared = gen.REMOTE.replace("__DIAG__", gen.DIAG_URL).strip("\n")
    for name in (N1, N2, N3):
        assert shared in code(name), f"{name} must inline the shared credential cell unchanged"
    assert "_REMOTE_FIELDS = {" in shared and "DIAGNOSTIC_PERSISTENCE_VERIFIED" in shared and "get_secret(name)" in shared
    assert "_CRED_CACHE[name] = raw.strip()" in shared and '_DIAG["key"] = ""' not in shared                   # only validated values are cached


def test_the_pinned_connector_revision_contains_serverctl_and_is_not_the_notebook_commit():
    rev = gen.ENIGMA_REV
    if subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{rev}^{{commit}}"], capture_output=True).returncode != 0:
        pytest.skip(f"{rev} not in this clone")
    show = lambda p: subprocess.run(["git", "-C", str(ROOT), "show", f"{rev}:{p}"], capture_output=True, text=True).stdout   # noqa: E731
    assert "def safe_stop" in show("connector/enigma_connector/serverctl.py") and "def open_loopback" in show("connector/enigma_connector/localnet.py")
    assert rev not in show(f"docs/kaggle/diagnostics/{N3}.ipynb")                                  # no circular pin
