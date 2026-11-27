"""CPU-only behaviour tests of the EXACT notebook 03 gate cells (taken from the generated notebook), with fake markers, a fake AWS diagnostic
transport and a fake HTTPS opener. Nothing here touches a GPU, Kaggle, Railway or AWS. These are not Kaggle acceptance."""
from __future__ import annotations

import importlib.util
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("nbgen", ROOT / "scripts" / "build_kaggle_diagnostic_notebooks.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)
N3 = "03_enigma_stokastisk_build5_aws_integration"
CELLS = ["".join(c["source"]) for c in json.loads((ROOT / "docs" / "kaggle" / "diagnostics" / f"{N3}.ipynb").read_text())["cells"] if c["cell_type"] == "code"]
CAP_HELPER = (ROOT / "scripts" / "kaggle_cells" / "capability.py").read_text()


def cell(marker):
    return next(c for c in CELLS if marker in c)


GATE = cell('verdict("runtime_capabilities", "PASS" if ok')
IDENTITY = cell('verdict("runtime_identity", "PASS"')
BASELINE = cell('verdict("baseline_manifest"')
HELPER = cell("def stage(name, **kw):")
OVERALL = cell('print(f"VERDICT overall')
STAGING_HEALTH = {"ok": True, "version": "0.1.0", "api_contract": 2, "store": "dynamodb", "auth": "cognito", "deployment": "aws-staging-isolated",
                  "data_plane": gen.DATA_PLANES["aws-staging"]}


class Stop(Exception):
    pass


class Resp:
    def __init__(self, status, body):
        self.status, self._b = status, body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self, n=-1):
        return self._b if n < 0 else self._b[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Opener:
    def __init__(self, status=200, body=None, delay=0.0, exc=None):
        self.status, self.body, self.delay, self.exc, self.requests = status, body, delay, exc, []

    def open(self, req, timeout=None):
        self.requests.append((req.full_url, dict(req.header_items())))
        time.sleep(self.delay)
        if self.exc:
            raise self.exc
        if self.status != 200:
            raise urllib.error.HTTPError(req.full_url, self.status, "x", {}, io.BytesIO(b"{}"))
        return Resp(200, self.body)


class Ack(dict):
    def __bool__(self):
        return self.get("code") == "DIAGNOSTIC_PERSISTENCE_VERIFIED"


@pytest.fixture
def nb():
    ns: dict = {}
    log, remote, results = [], [], {}
    st = {"ack": True, "ack_after": None, "opener": Opener(body=STAGING_HEALTH)}

    def mark(name, **f):
        log.append((name, json.dumps(f, default=str)))
        return {}

    def rmark(name, **f):
        mark(name, **f)
        remote.append(name)
        ok = st["ack"] and (st["ack_after"] is None or len(remote) <= st["ack_after"])
        return Ack(code="DIAGNOSTIC_PERSISTENCE_VERIFIED" if ok else "DIAGNOSTIC_HTTP_ERROR")

    def verdict(step, status, reason, **ev):
        results[step] = {"status": status, "reason": reason, "attempt": "A1", **ev}
        if status != "PASS":
            raise Stop(f"{step} {status}: {reason}")

    def rcheck(step, name, **f):
        r = rmark(name, **f)
        if not r:
            verdict(step, "INCONCLUSIVE", "not durable")
        return r

    def require(*steps):
        bad = [s for s in steps if results.get(s, {}).get("status") != "PASS"]
        if bad:
            raise Stop(f"prereq {bad}")

    ns.update(json=json, time=time, urllib=urllib, mark=mark, rmark=rmark, rcheck=rcheck, verdict=verdict, require=require, RESULTS=results,
              ATTEMPT="A1", RUNTIME_TRANSPORT="aws", INTEGRATION_MODE="aws-staging", ALLOW_RAILWAY_COMPARISON=False, DiagnosticStop=Stop,
              get_credential=lambda n: ("enig_rt_abcd1234_" + "S" * 43, {"code": "OK"}))
    exec(compile(CAP_HELPER, "capability.py", "exec"), ns)
    ns["_cap_opener"] = lambda: st["opener"]
    ns.update(_log=log, _remote=remote, _st=st)
    for s in ("baseline_manifest", "aws_endpoint", "runtime_token_preflight"):
        results[s] = {"status": "PASS", "attempt": "A1"}
    return ns


def run(ns, src):
    exec(compile(src, "cell", "exec"), ns)


# ---------------------------------------------------------------------------------------------------------------- the hardened probe helper itself
def test_probe_refuses_other_hosts_plain_http_and_userinfo_without_any_request(nb):
    for url in ("http://x.lambda-url.us-east-1.on.aws", "https://evil.example", "https://u:p@x.lambda-url.us-east-1.on.aws", "https://x.lambda-url.us-east-1.on.aws:8443"):
        assert nb["probe_health"](url, "x.lambda-url.us-east-1.on.aws")["http"]["error"] == "endpoint_not_allowed"
    assert nb["_st"]["opener"].requests == []


def test_probe_sends_no_credential_refuses_redirects_and_bounds_body_and_time(nb):
    host, url = "x.lambda-url.us-east-1.on.aws", "https://x.lambda-url.us-east-1.on.aws"
    r = nb["probe_health"](url, host)
    assert r["http"]["result"] == "PASS" and r["body"]["deployment"] == "aws-staging-isolated"
    sent = nb["_st"]["opener"].requests[0][1]
    assert not {k.lower() for k in sent} & {"authorization", "cookie"}
    nb["_st"]["opener"] = Opener(status=302)
    assert nb["probe_health"](url, host)["http"]["error"] == "redirect_refused"
    nb["_st"]["opener"] = Opener(body=b"x" * 5000)
    assert nb["probe_health"](url, host)["http"]["error"] == "body_too_large"
    nb["_st"]["opener"] = Opener(body=STAGING_HEALTH, delay=1.0)
    t = time.monotonic()
    assert nb["probe_health"](url, host, wall_timeout=0.2)["http"]["error"] == "wall_clock_timeout" and time.monotonic() - t < 0.9


def test_schema_requires_typed_fields_ok_true_and_the_contract(nb):
    cs = nb["check_schema"]
    assert cs(STAGING_HEALTH, 2)["result"] == "PASS"
    assert cs({**STAGING_HEALTH, "api_contract": True}, 2)["result"] == "FAIL"                       # a bool is not an int
    assert cs({k: v for k, v in STAGING_HEALTH.items() if k != "data_plane"}, 2)["result"] == "FAIL"     # the pre-contract-2 health response
    assert cs({**STAGING_HEALTH, "api_contract": 1}, 2)["result"] == "FAIL" and cs({**STAGING_HEALTH, "ok": False}, 2)["result"] == "FAIL"
    assert cs(["not", "a", "dict"], 2)["result"] == "FAIL"


# ---------------------------------------------------------------------------------------------------------------- the capability gate cell
def test_aws_staging_passes_with_four_independent_results_and_contacts_only_aws(nb):
    run(nb, GATE)
    r = nb["RESULTS"]["runtime_capabilities"]
    assert r["status"] == "PASS" and r["scope"] == "STAGING" and set(r["capability"].values()) == {"PASS"} and len(r["capability"]) == 4
    assert [urllib.parse.urlsplit(u).hostname for u, _ in nb["_st"]["opener"].requests] == [urllib.parse.urlsplit(gen.AWS_RUNTIME_URL).hostname]
    assert nb["_remote"] == ["before_capability_probe", "after_capability_probe"]


@pytest.mark.parametrize("override", [{"deployment": "production"}, {"data_plane": gen.DATA_PLANES["aws-production"]}, {"store": "postgres"},
                                      {"auth": "neon"}])
def test_a_200_with_the_wrong_deployment_or_data_plane_is_not_readiness(nb, override):
    nb["_st"]["opener"] = Opener(body={**STAGING_HEALTH, **override})
    with pytest.raises(Stop):
        run(nb, GATE)
    r = nb["RESULTS"]["runtime_capabilities"]
    assert r["status"] == "INCONCLUSIVE" and r["capability"]["CAPABILITY_HTTP_RESULT"] == "PASS" and r["capability"]["CAPABILITY_DEPLOYMENT_RESULT"] == "FAIL"


def test_aws_production_is_blocked_before_any_request_or_marker(nb):
    nb["INTEGRATION_MODE"] = "aws-production"
    with pytest.raises(Stop, match="BLOCKED"):
        run(nb, GATE)
    assert nb["RESULTS"]["runtime_capabilities"]["blocked"] is True and nb["_st"]["opener"].requests == [] and nb["_remote"] == []


def test_railway_comparison_needs_explicit_authorization(nb):
    nb.update(INTEGRATION_MODE="railway-comparison", RUNTIME_TRANSPORT="railway")
    with pytest.raises(Stop, match="BLOCKED"):
        run(nb, GATE)
    assert nb["_st"]["opener"].requests == []
    nb["ALLOW_RAILWAY_COMPARISON"] = True
    nb["_st"]["opener"] = Opener(body={**STAGING_HEALTH, "store": "postgres", "auth": "neon", "deployment": "production",
                                       "data_plane": gen.DATA_PLANES["railway-comparison"]})
    run(nb, GATE)
    assert nb["RESULTS"]["runtime_capabilities"]["scope"] == "RAILWAY-COMPARISON"
    assert urllib.parse.urlsplit(nb["_st"]["opener"].requests[0][0]).hostname == urllib.parse.urlsplit(gen.API_URL).hostname


def test_an_unacknowledged_before_marker_stops_before_the_probe(nb):
    nb["_st"]["ack"] = False
    with pytest.raises(Stop):
        run(nb, GATE)
    assert nb["_st"]["opener"].requests == []


def test_an_unacknowledged_after_marker_fails_durable_evidence(nb):
    nb["_st"]["ack_after"] = 1
    with pytest.raises(Stop):
        run(nb, GATE)
    assert nb["RESULTS"]["runtime_capabilities"]["capability"]["CAPABILITY_DURABLE_EVIDENCE_RESULT"] == "FAIL"


# ---------------------------------------------------------------------------------------------------------------- real runtime authentication
def _identity_ready(nb):
    run(nb, GATE)


def test_identity_requires_a_real_runtime_and_never_records_the_token(nb):
    _identity_ready(nb)
    nb["_st"]["opener"] = Opener(body={"runtime_id": "rt1", "project_id": "p1", "is_mock": False, "status": "online"})
    run(nb, IDENTITY)
    assert nb["RESULTS"]["runtime_identity"]["status"] == "PASS"
    url, headers = nb["_st"]["opener"].requests[0]
    assert url.endswith("/api/v1/runtimes/me") and headers["Authorization"].startswith("Bearer enig_rt_")
    assert not any("SSSSSSSS" in f for _, f in nb["_log"])
    nb["_st"]["opener"] = Opener(body={"runtime_id": "rt1", "is_mock": True})
    with pytest.raises(Stop):
        run(nb, IDENTITY)


def test_identity_401_explains_a_token_for_the_wrong_mode(nb):
    _identity_ready(nb)
    nb["_st"]["opener"] = Opener(status=401)
    with pytest.raises(Stop, match="not accepted by THIS endpoint"):
        run(nb, IDENTITY)


# ---------------------------------------------------------------------------------------------------------------- historical baseline manifest
def test_baseline_manifest_is_digest_verified_and_matches_the_validated_environment(nb):
    nb.update(vers={"torch": "2.11.0+cu130", "triton": "3.6.0", "transformers": "5.20.2"}, names=["Tesla T4", "Tesla T4"])
    nb["RESULTS"]["environment"] = {"status": "PASS", "attempt": "A1"}
    run(nb, BASELINE)
    r = nb["RESULTS"]["baseline_manifest"]
    assert r["status"] == "PASS" and r["reason"].startswith("HISTORICAL") and all(r["checks"].values())


def test_a_different_environment_or_a_tampered_manifest_is_not_accepted(nb):
    nb["RESULTS"]["environment"] = {"status": "PASS", "attempt": "A1"}
    nb.update(vers={"torch": "2.12.0+cu130", "triton": "3.6.0", "transformers": "5.20.2"}, names=["Tesla T4", "Tesla T4"])
    with pytest.raises(Stop):
        run(nb, BASELINE)
    assert nb["RESULTS"]["baseline_manifest"]["checks"]["environment_matches_baseline"] is False
    nb.update(vers={"torch": "2.11.0+cu130", "triton": "3.6.0", "transformers": "5.20.2"})
    with pytest.raises(Stop):
        tampered = BASELINE.replace('"BUILD5_SAFE_SHUTDOWN":"PASS"', '"BUILD5_SAFE_SHUTDOWN":"FAIL"')
        assert tampered != BASELINE
        run(nb, tampered)
    assert nb["RESULTS"]["baseline_manifest"]["checks"]["manifest_digest"] is False


def test_the_committed_manifest_matches_the_evidence_when_it_is_available():
    import os
    ev = Path(os.environ.get("ENIGMA_KAGGLE_EVIDENCE_DIR", "/nonexistent"))
    if not (ev / "02-stokastisk-build5-kaggle-t4-validation-enigma-diag-attempts.zip").exists():
        pytest.skip("set ENIGMA_KAGGLE_EVIDENCE_DIR to the directory holding the owner's executed notebooks and archives")
    import subprocess, sys
    r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "build_baseline_manifest.py"), str(ev), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------------------------------------------------------- stage helper and overall verdict
def _stage_ns(nb, passed=True, after_ack=True):
    class Srv:
        base_url = "http://127.0.0.1:8000"
        def health(self):
            return True
    nb["_st"]["ack"] = True
    calls = []

    def run_stage(name, **kw):
        calls.append((name, kw["api_url"]))
        if not after_ack:
            nb["_st"]["ack"] = False
        return {"passed": passed, "checks": [("x", passed, "")], "exit": {"returncode": 0 if passed else 3}, "summary": {"stats": {}}}
    for s in ("connector_fetch", "local_inference"):
        nb["RESULTS"][s] = {"status": "PASS", "attempt": "A1"}
    src = HELPER.replace("from enigma_connector.stage_runner import run_stage, MarkerLog\nfrom enigma_connector import serverctl\n", "")
    nb.update(server=Srv(), MODEL="m", API_URL="https://x", ATTEMPT_DIR="/nonexistent", token_getter=None, MarkerLog=lambda *a, **k: None)
    run(nb, src)
    nb["run_stage"] = run_stage
    return calls


def test_a_passed_stage_without_durable_evidence_cannot_satisfy_the_next_stage(nb):
    calls = _stage_ns(nb, passed=True, after_ack=False)
    with pytest.raises(Stop):
        nb["stage"]("nvml")
    assert nb["RESULTS"]["stage_nvml"]["status"] == "PASS" and nb["RESULTS"]["stage_nvml_evidence"]["status"] == "INCONCLUSIVE"
    assert calls == [("nvml", "")]                                                                     # offline: no API URL at all
    nb["_st"]["ack"] = True
    with pytest.raises(Stop, match="prereq"):
        nb["stage"]("vllm")


def test_a_failed_stage_stays_failed_even_with_its_marker_stored(nb):
    _stage_ns(nb, passed=False)
    with pytest.raises(Stop):
        nb["stage"]("nvml")
    assert nb["RESULTS"]["stage_nvml"]["status"] == "FAIL" and "stage_nvml_evidence" not in nb["RESULTS"]


def test_cloud_stages_require_the_cloud_gate(nb):
    _stage_ns(nb)
    for s in ("nvml", "vllm"):
        nb["stage"](s)
    with pytest.raises(Stop, match="cloud_gate"):
        nb["stage"]("register")


def _all_pass(nb):
    req = ["environment", "diag_key_preflight", "runtime_token_preflight", "aws_endpoint", "baseline_manifest", "runtime_capabilities", "runtime_identity",
           "post_capability_survival", "wheel_verified", "install_plan", "install", "server_start", "local_inference", "connector_fetch", "cloud_gate",
           *[f"stage_{s}{e}" for s in ("nvml", "vllm", "register", "heartbeat", "jobs", "upload-once", "telemetry", "full") for e in ("", "_evidence")],
           "server_shutdown"]
    for s in req:
        nb["RESULTS"][s] = {"status": "PASS", "attempt": "A1"}
    nb["SCOPE"] = "STAGING"


def test_overall_pass_is_labelled_staging_not_production(nb, capsys):
    _all_pass(nb)
    run(nb, OVERALL)
    assert nb["RESULTS"]["overall"]["status"] == "PASS" and "NOT production acceptance" in capsys.readouterr().out


@pytest.mark.parametrize("case,expected", [("no_shutdown", "INCONCLUSIVE"), ("failed_stage", "FAIL"), ("blocked", "BLOCKED"), ("final_unacked", "INCONCLUSIVE")])
def test_overall_never_passes_early_or_without_evidence(nb, case, expected):
    _all_pass(nb)
    if case == "no_shutdown":
        del nb["RESULTS"]["server_shutdown"]
    elif case == "failed_stage":
        nb["RESULTS"]["stage_full"]["status"] = "FAIL"
    elif case == "blocked":
        nb["RESULTS"] = {"runtime_capabilities": {"status": "INCONCLUSIVE", "attempt": "A1", "blocked": True}}
    else:
        nb["_st"]["ack"] = False
    with pytest.raises(Stop):
        run(nb, OVERALL)
    assert nb["RESULTS"]["overall"]["status"] == expected
