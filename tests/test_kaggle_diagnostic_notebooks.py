"""The six Kaggle diagnostic notebooks (docs/kaggle/diagnostics/) are generated, independent and honest. CPU-only checks: nothing here runs a GPU step."""
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


def cells(name):
    return json.loads((DIR / f"{name}.ipynb").read_text())["cells"]


def code(name):
    return ["".join(c["source"]) for c in cells(name) if c["cell_type"] == "code"]


@pytest.mark.parametrize("name", list(gen.NOTEBOOKS))
def test_committed_notebook_equals_generator_output_and_every_cell_parses(name):
    assert (DIR / f"{name}.ipynb").read_text() == gen.render(name), "regenerate: python3.13 scripts/build_kaggle_diagnostic_notebooks.py"
    nb = json.loads((DIR / f"{name}.ipynb").read_text())
    assert nb["nbformat"] == 4 and all(c.get("outputs", []) == [] and c.get("execution_count") is None for c in nb["cells"] if c["cell_type"] == "code")
    for src in code(name):
        ast.parse(src)


@pytest.mark.parametrize("name", list(gen.NOTEBOOKS))
def test_each_notebook_starts_with_its_own_guarded_prelude(name):
    first = code(name)[0]
    assert f'NOTEBOOK = "{name}"' in first and f'EXPECT = "{gen.NOTEBOOKS[name]}"' in first
    assert 'os.makedirs(ATTEMPT_DIR, exist_ok=False)' in first and "class DiagnosticStop" in first
    assert 'assert os.path.isdir("/kaggle/working")' in first
    assert "os.environ[k] for k in (\"KAGGLE_KERNEL_RUN_TYPE\", \"KAGGLE_DOCKER_IMAGE\")" in first and "dict(os.environ)" not in first


@pytest.mark.parametrize("name", [n for n, e in gen.NOTEBOOKS.items() if e == "cpu"])
def test_cpu_notebooks_never_touch_gpu_libraries_stokastisk_or_vllm(name):
    text = "\n".join(code(name))
    assert not re.search(r"^\s*(import|from)\s+(torch|pynvml|vllm|stokastisk|triton)\b", text, re.M)
    assert "nvidia-smi" not in text and "NvmlSampler" not in text and "run_stage(" not in text
    assert not re.search(r'"pip", "install"(?![^\]]*--dry-run)', text), "CPU notebooks never install anything"


def test_notebook_a_runs_the_idle_control_before_any_network_or_clone():
    srcs = code("enigma_kaggle_cpu_platform_baseline")
    idle = next(i for i, s in enumerate(srcs) if 'observe("A2_idle_10min", 600' in s)
    first_net = next(i for i, s in enumerate(srcs) if "urlopen" in s or "pip" in s)
    assert idle < first_net and not any("git" in s and "clone" in s for s in srcs)
    assert not any("ENIGMA_RUNTIME_TOKEN" in s or "kaggle_secrets" in s for s in srcs)


def test_notebooks_c_and_d_send_no_enigma_traffic_and_read_no_credential():
    for name in ("stokastisk_kaggle_gpu_platform_baseline", "stokastisk_kaggle_vllm_stability"):
        text = "\n".join(code(name))
        assert "railway.app" not in text and "kaggle_secrets" not in text and "enigma_connector" not in text


def test_notebook_e_never_sends_a_registration():
    text = "\n".join(code("enigma_kaggle_registration_without_vllm"))
    assert 'run_stage("register"' not in text and "kaggle_secrets" not in text and "railway.app" not in text
    assert "UNSUPPORTED" in text and 'run_stage("nvml"' in text


def test_final_integration_is_gated_and_ordered_and_requires_a_real_job():
    text = "\n".join(code("enigma_stokastisk_qwen25_05b_final_integration"))
    assert "BASELINES_PASSED = False" in text and "ALLOW_CLOUD_CONNECTION = False" in text
    order = [m.group(1) for m in re.finditer(r'stage\("([a-z-]+)"', text)]
    assert order == ["nvml", "vllm", "register", "heartbeat", "jobs", "upload-once", "telemetry", "full"]
    assert '(stats.get("jobs_done") or 0) >= 1' in text
    assert '"--gpu-interval", "1.0", "--vllm-interval", "2.0", "--upload-interval", "5.0"' in text


@pytest.mark.parametrize("name", list(gen.NOTEBOOKS))
def test_no_notebook_prints_or_embeds_a_credential(name):
    text = "\n".join(code(name))
    assert not re.search(r"enig_rt_[a-z0-9]{8}_[A-Za-z0-9_-]{10,}", text)
    assert not re.search(r"print\([^)]*(token|get_secret|_v\b)", text, re.I)
    assert not re.search(r"mark\([^)]*(get_secret|token_getter\(\))", text)
    assert "os.environ[\"ENIGMA_RUNTIME_TOKEN\"]" not in text and "dict(os.environ)" not in text
    assert "postgres://" not in text and "postgresql://" not in text


def _prelude_namespace(tmp_path, expect="cpu"):
    src = code("enigma_kaggle_cpu_platform_baseline")[0].replace("/kaggle/working", str(tmp_path)).replace('os.path.exists("/dev/nvidia0")', "False")
    src = src.replace('EXPECT = "cpu"', f'EXPECT = "{expect}"')
    (tmp_path).mkdir(exist_ok=True)
    g: dict = {"__name__": "nb"}
    exec(compile(src, "prelude", "exec"), g)
    return g


def test_a_non_pass_verdict_stops_execution_and_stale_or_missing_prerequisites_are_refused(tmp_path):
    g = _prelude_namespace(tmp_path)
    g["verdict"]("ok_step", "PASS", "fine")
    g["require"]("ok_step")
    with pytest.raises(g["DiagnosticStop"]):
        g["verdict"]("bad_step", "FAIL", "broken")
    with pytest.raises(g["DiagnosticStop"]):
        g["verdict"]("unsure", "INCONCLUSIVE", "wrong environment")
    with pytest.raises(g["DiagnosticStop"]):
        g["require"]("never_ran")
    g["RESULTS"]["old"] = {"status": "PASS", "attempt": "an-earlier-session"}
    with pytest.raises(g["DiagnosticStop"]):
        g["require"]("old")
    with pytest.raises(AssertionError):
        g["verdict"]("x", "OK", "not a valid status")


def test_the_wrong_environment_is_refused_before_anything_runs(tmp_path):
    with pytest.raises(Exception, match="wrong environment"):
        _prelude_namespace(tmp_path, expect="gpu")                # 'no GPU device' (patched) but the notebook expects one


def test_markers_are_fsynced_jsonl_with_identity_and_host_evidence(tmp_path):
    g = _prelude_namespace(tmp_path)
    rec = g["mark"]("probe", note="x")
    for key in ("attempt", "utc", "t", "pid", "pgid", "sid", "ppid", "memavailable_mib", "cgroup_events", "vm_uptime_s"):
        assert key in rec
    lines = Path(g["ATTEMPT_DIR"], "markers.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["marker"] == "probe"


def test_the_pinned_connector_revision_contains_the_reviewed_fixes():
    rev = gen.ENIGMA_REV
    if subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{rev}^{{commit}}"], capture_output=True).returncode != 0:
        pytest.skip(f"{rev} not in this clone")
    show = lambda p: subprocess.run(["git", "-C", str(ROOT), "show", f"{rev}:{p}"], capture_output=True, text=True).stdout   # noqa: E731
    assert "def finite_number" in show("connector/enigma_connector/config.py")
    assert "class ProbeWorkerError" in show("connector/enigma_connector/probe.py")
    assert "_stop_lock" in show("connector/enigma_connector/supervise.py") and "pid_is_not_our_session_leader" in show("connector/enigma_connector/supervise.py")
