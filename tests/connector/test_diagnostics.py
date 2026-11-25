"""Diagnostic isolation, crash-evidence and supervision tooling. CPU only: no NVML, no CUDA, no GPU.

Covers: registration without background threads, independently enabled subsystems, thread/HTTP/upload failures, bounded reads, graceful and forced
shutdown of a supervised child, signal-death reporting with faulthandler evidence, SDK metadata completeness, credential redaction, and the mock-runtime
guard. (Tenant isolation is covered by tests/api/test_auth_and_tenancy.py and tests/api/test_neon_auth.py; the API is unchanged.)"""
from __future__ import annotations

import io
import json
import os
import signal
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest

from enigma_api.models import SdkIdentity
from enigma_connector import cli
from enigma_connector.config import Config
from enigma_connector.diag import DiagLog, FaultHandler, redact
from enigma_connector.http import MAX_RESPONSE_BYTES, ApiClient, ApiError, Transient
from enigma_connector.identity import build_tag_from_wheel_file, collect_sdk_identity, fetch_vllm_version, sha256_from_direct_url
from enigma_connector.mock import MockGpu, MockLlm, MockVllm
from enigma_connector.probe import run_local_nvml, run_local_vllm
from enigma_connector.runner import FEATURES, Connector
from enigma_connector.spool import Spool
from enigma_connector.supervise import ConnectorProcess
from enigma_connector.vllm_metrics import MAX_METRICS_BYTES, VllmSampler

TOKEN = "enig_rt_abcdefgh_" + "A" * 43
SHA = "fe80d7c36fa78cf8a26feb1e00bb5f1f006ca45a027450353603976320ae0b3e"
CONNECTOR_DIR = str(Path(__file__).resolve().parents[2] / "connector")


def wait_for(fn, timeout=8.0, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condition not met in time")


# --------------------------------------------------------------------------------------------- a recording fake API (no network at all)
class FakeApi:
    def __init__(self, fail: dict[str, object] | None = None, big: set[str] | None = None):
        self.paths: list[str] = []
        self.fail, self.big = fail or {}, big or set()
        self.lock = threading.Lock()

    def __call__(self, req, timeout=None):
        path = req.full_url.split("api.test", 1)[1]
        with self.lock:
            self.paths.append(path)
        key = next((k for k in self.fail if k in path), None)
        if key is not None:
            f = self.fail[key]
            if isinstance(f, int):
                raise urllib.error.HTTPError(req.full_url, f, "x", {}, io.BytesIO(json.dumps({"error": {"code": f"http_{f}"}}).encode()))
            raise f                                                                   # type: ignore[misc]
        body: object = {"ok": True}
        if path.endswith("/register"):
            body = {"run_id": "run-1", "runtime_id": "rt-1"}
        elif path.endswith("/claim"):
            body = {"job": None, "poll_after_ms": 1000}
        raw = json.dumps(body).encode()
        if any(b in path for b in self.big):
            raw = b" " * (MAX_RESPONSE_BYTES + 10)
        resp = io.BytesIO(raw)
        resp.__enter__ = lambda *a: resp                                             # type: ignore[attr-defined]
        resp.__exit__ = lambda *a: False                                             # type: ignore[attr-defined]
        return resp

    def count(self, part: str) -> int:
        with self.lock:
            return sum(part in p for p in self.paths)


def make(tmp_path, fake: FakeApi, features=None, diag=None, **cfg_over):
    cfg = Config(api_url="https://api.test", token=TOKEN, instance_id="t", mode="mock", served_model="m", heartbeat_interval_s=0.05, gpu_interval_s=0.02,
                 vllm_interval_s=0.02, upload_interval_s=0.05, claim_poll_s=0.02, claim_poll_idle_s=0.0, **cfg_over).validate()
    mv = MockVllm()
    return Connector(cfg, ApiClient(cfg, opener=fake), MockLlm(0.001, 0.001, vllm=mv), MockGpu(), mv, Spool(str(tmp_path / "spool")), is_mock=True,
                     features=features, diag=diag)


# --------------------------------------------------------------------------------------------- redaction and the crash-proof log
def test_redact_covers_runtime_keys_bearer_jwt_dsn_and_nested_values():
    jwt_like = "eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLWJ5dGVz"
    out = redact({"a": f"key {TOKEN} here", "b": [f"Authorization: Bearer {jwt_like}", "postgresql://u:pw@host/db?sslmode=verify-full"], "n": 3,
                  "c": {"password=hunter2": "token: abc123"}})
    blob = json.dumps(out)
    for needle in (TOKEN, "A" * 43, jwt_like, "pw@host", "hunter2", "abc123"):
        assert needle not in blob, needle
    assert out["n"] == 3 and "enig_rt_<redacted>" in blob


def test_diag_log_is_append_only_fsynced_per_line_and_redacted(tmp_path, monkeypatch):
    synced = []
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd))
    d = DiagLog(tmp_path / "x" / "d.jsonl")
    d.emit("one", note=f"token {TOKEN}")
    d.emit("two", n=2)
    d.close()
    DiagLog(tmp_path / "x" / "d.jsonl").emit("three")                              # re-open: appends, never truncates
    lines = [json.loads(line) for line in (tmp_path / "x" / "d.jsonl").read_text().splitlines()]
    assert [x["event"] for x in lines] == ["one", "two", "three"]
    assert TOKEN not in (tmp_path / "x" / "d.jsonl").read_text()
    assert len(synced) >= 3, "every line must be fsynced so the last line survives a hard kill"


def test_diag_failure_never_raises_into_the_connector(tmp_path):
    d = DiagLog(tmp_path / "d.jsonl")
    d._fh.close()                                                                  # simulate a broken/full disk
    d.emit("after_close", x=object())                                              # must not raise


# --------------------------------------------------------------------------------------------- independently enabled subsystems
def test_register_stage_registers_and_starts_no_background_thread(tmp_path):
    fake = FakeApi()
    c = make(tmp_path, fake, features=cli.STAGES["register"])
    c.start()
    time.sleep(0.3)
    assert fake.paths == ["/api/v1/runtimes/register"] and c._threads == []
    c.stop()
    assert fake.paths == ["/api/v1/runtimes/register"], "stopping a register-only connector must not send anything"


def test_heartbeat_only_stage_sends_heartbeats_and_nothing_else(tmp_path):
    fake = FakeApi()
    c = make(tmp_path, fake, features=cli.STAGES["heartbeat"])
    c.start()
    wait_for(lambda: fake.count("/heartbeat") >= 3)
    c.stop()
    assert fake.count("/jobs/claim") == 0 and fake.count("/telemetry") == 0 and [t.name for t in c._threads] == ["enigma-heartbeat"]


def test_jobs_only_stage_polls_claims_and_nothing_else(tmp_path):
    fake = FakeApi()
    c = make(tmp_path, fake, features=cli.STAGES["jobs"])
    c.start()
    wait_for(lambda: fake.count("/jobs/claim") >= 3)
    c.stop()
    assert fake.count("/heartbeat") == 0 and fake.count("/telemetry") == 0


def test_telemetry_stage_has_no_job_claiming_and_uploads_batches(tmp_path):
    fake = FakeApi()
    c = make(tmp_path, fake, features=cli.STAGES["telemetry"])
    c.start()
    wait_for(lambda: fake.count("/telemetry/batch") >= 2 and fake.count("/heartbeat") >= 1)
    c.stop()
    assert fake.count("/jobs/claim") == 0


def test_upload_once_stage_samples_locally_then_sends_exactly_one_final_flush(tmp_path):
    fake = FakeApi()
    c = make(tmp_path, fake, features=cli.STAGES["upload-once"])
    c.start()
    time.sleep(0.3)                                                                # sampling for a while ...
    assert fake.count("/telemetry") == 0, "no periodic upload in this stage"
    c.stop()
    assert fake.count("/telemetry/batch") == 1 and fake.count("/heartbeat") == 0 and fake.count("/jobs/claim") == 0


def test_unknown_feature_is_rejected_and_defaults_are_unchanged(tmp_path):
    with pytest.raises(ValueError):
        make(tmp_path, FakeApi(), features={"heartbeat", "warp-drive"})
    assert make(tmp_path, FakeApi()).features == FEATURES


# --------------------------------------------------------------------------------------------- failures
def test_connector_thread_error_is_recorded_and_stops_the_connector(tmp_path):
    fake = FakeApi(fail={"/heartbeat": RuntimeError("boom")})
    d = DiagLog(tmp_path / "d.jsonl")
    c = make(tmp_path, fake, features=cli.STAGES["heartbeat"], diag=d)
    c.start()
    wait_for(lambda: c.stop_event.is_set())
    c.stop()
    assert isinstance(c.fatal, RuntimeError)
    events = [json.loads(line)["event"] for line in (tmp_path / "d.jsonl").read_text().splitlines()]
    assert "thread_crashed" in events and "register_ok" in events


def test_http_500_on_telemetry_keeps_the_batch_in_the_spool_and_422_drops_it(tmp_path):
    c = make(tmp_path, FakeApi(fail={"/telemetry": 503}), features=cli.STAGES["telemetry"])
    c.start()
    wait_for(lambda: c.stats["batches_spooled"] >= 1)
    c.stop()
    assert c.stats["batches_sent"] == 0 and len(c.spool) >= 1
    d = make(tmp_path / "b", FakeApi(fail={"/telemetry": 422}), features=cli.STAGES["telemetry"])
    d.start()
    wait_for(lambda: d.stats["batches_dropped"] >= 1)
    d.stop()
    assert d.stats["batches_sent"] == 0 and len(d.spool) == 0, "a validation failure must not block the queue"


def test_oversized_api_reply_is_refused_not_buffered(tmp_path):
    cfg = Config(api_url="https://api.test", token=TOKEN, instance_id="t", mode="mock").validate()
    with pytest.raises(Transient, match="response_too_large"):
        ApiClient(cfg, opener=FakeApi(big={"/heartbeat"})).post("/api/v1/runtimes/x/heartbeat")


def test_oversized_vllm_metrics_are_refused_not_buffered(monkeypatch):
    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=0: Resp(b"#" * (MAX_METRICS_BYTES + 10)))
    s = VllmSampler("http://127.0.0.1:8000")
    assert s.sample() is None and s.last_error == "ValueError"


# --------------------------------------------------------------------------------------------- offline probes (no network, no token)
class FakeNvml:
    def __init__(self, fail_after=None):
        self.n, self.fail_after, self._unsupported = 0, fail_after, {(0, "pcie_rx")}
    def inventory(self): return [{"index": 0, "name": "FAKE T4", "memory_total_mib": 15360.0}, {"index": 1, "name": "FAKE T4", "memory_total_mib": 15360.0}]
    def sample(self):
        self.n += 1
        if self.fail_after and self.n > self.fail_after:
            raise OSError("driver went away")
        return [{"mem_used_mib": 100.0 * (i + 1), "temp_c": 40.0, "power_w": 20.0, "util_gpu_pct": 1.0} for i in range(2)]


def test_nvml_probe_runs_in_a_background_thread_without_touching_the_network(tmp_path):
    d = DiagLog(tmp_path / "p.jsonl")
    res = run_local_nvml(FakeNvml(), d, threading.Event(), interval_s=0.01, duration_s=0.15)
    assert res["samples"] >= 5 and res["errors"] == 0 and res["unsupported_counters"] == ["gpu0:pcie_rx"]
    ev = [json.loads(line) for line in (tmp_path / "p.jsonl").read_text().splitlines()]
    assert ev[0]["event"] == "nvml_inventory" and ev[-1]["event"] == "nvml_probe_done"


def test_nvml_probe_records_a_failing_sample_and_keeps_going(tmp_path):
    res = run_local_nvml(FakeNvml(fail_after=2), DiagLog(tmp_path / "p.jsonl"), threading.Event(), interval_s=0.01, duration_s=0.12)
    assert res["samples"] == 2 and res["errors"] >= 1


def test_vllm_probe_reports_an_unreachable_server_without_raising(tmp_path):
    res = run_local_vllm(VllmSampler("http://127.0.0.1:9"), DiagLog(tmp_path / "v.jsonl"), threading.Event(), interval_s=0.01, duration_s=0.1)
    assert res["scrapes"] >= 2 and res["empty"] == res["scrapes"]


def test_cli_nvml_stage_is_refused_on_a_no_gpu_host_before_nvml_is_imported(tmp_path, monkeypatch):
    monkeypatch.setenv("ENIGMA_FORBID_LOCAL_GPU", "1")
    monkeypatch.setitem(sys.modules, "pynvml", None)                               # importing it would raise: proves it is never imported
    assert cli.main(["--stage", "nvml", "--duration", "1", "--diag-log", str(tmp_path / "d.jsonl")]) == cli.EXIT_REFUSED
    assert "local_gpu_forbidden" in (tmp_path / "d.jsonl").read_text()


# --------------------------------------------------------------------------------------------- SDK metadata
class FakeDist:
    version = "0.1.0"
    def __init__(self, wheel="Wheel-Version: 1.0\nBuild: 5\nTag: cp313-cp313-linux_x86_64\n", direct=None):
        self._f = {"WHEEL": wheel, "direct_url.json": json.dumps(direct) if direct is not None else None}
    def read_text(self, name): return self._f.get(name)


def test_sdk_identity_is_complete_when_the_evidence_exists_and_valid_for_the_api():
    dist = FakeDist(direct={"url": "https://huggingface.co/x.whl", "archive_info": {"hashes": {"sha256": SHA}}})
    ident = collect_sdk_identity("http://127.0.0.1:8000", dist=dist, vllm_version_fn=lambda u: "0.25.1")
    assert {"stokastisk_version": "0.1.0", "stokastisk_build": "5", "wheel_sha256": SHA, "vllm_version": "0.25.1"}.items() <= ident.items()
    assert ident["python_version"].startswith("3.")
    SdkIdentity(**ident)                                                           # exactly what POST /runtimes/register will validate


def test_sdk_identity_omits_what_it_cannot_verify_and_never_invents_a_hash():
    ident = collect_sdk_identity(None, dist=FakeDist(wheel="Wheel-Version: 1.0\n", direct=None), vllm_package_fn=lambda: None)
    assert "wheel_sha256" not in ident and "stokastisk_build" not in ident and "vllm_version" not in ident
    assert sha256_from_direct_url(json.dumps({"archive_info": {"hash": "sha256=" + "z" * 64}})) is None          # not hex
    assert sha256_from_direct_url(json.dumps({"archive_info": {"hash": "sha256=" + SHA.upper()}})) == SHA          # legacy spelling, normalised
    assert sha256_from_direct_url("not json") is None and build_tag_from_wheel_file("Build: not a tag!") is None


def test_vllm_version_falls_back_to_the_installed_package_when_the_server_does_not_answer():
    d = FakeDist()
    assert collect_sdk_identity("http://127.0.0.1:8000", dist=d, vllm_version_fn=lambda u: None, vllm_package_fn=lambda: "0.25.1")["vllm_version"] == "0.25.1"
    assert collect_sdk_identity("http://127.0.0.1:8000", dist=d, vllm_version_fn=lambda u: "0.25.2", vllm_package_fn=lambda: "0.25.1")["vllm_version"] == "0.25.2"
    assert "vllm_version" not in collect_sdk_identity("http://127.0.0.1:8000", dist=d, vllm_version_fn=lambda u: None, vllm_package_fn=lambda: None)


def test_vllm_version_is_only_fetched_from_loopback():
    assert fetch_vllm_version("https://example.com") is None and fetch_vllm_version("http://10.0.0.5:8000") is None


# --------------------------------------------------------------------------------------------- supervised child process
def _child(script: str, tmp_path, **kw) -> ConnectorProcess:
    return ConnectorProcess([], {**os.environ, "ENIGMA_RUNTIME_TOKEN": TOKEN}, tmp_path / "proc", command=[sys.executable, "-c", script], **kw)


def test_supervised_child_exits_cleanly_on_sigterm_within_the_grace_period(tmp_path):
    p = _child("import signal,sys,time\nsignal.signal(signal.SIGTERM, lambda *a: sys.exit(0))\nopen('ready','w').write('1')\nwhile True: time.sleep(0.05)", tmp_path)
    p.start()
    wait_for(lambda: (tmp_path / "proc" / "ready").exists())
    t0 = time.monotonic()
    rep = p.stop(grace_s=5, kill_after_s=2)
    assert rep["returncode"] == 0 and rep["escalated_to_sigkill"] is False and time.monotonic() - t0 < 4


def test_supervised_child_that_ignores_sigterm_is_killed_within_a_bounded_time(tmp_path):
    p = _child("import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nopen('ready','w').write('1')\nwhile True: time.sleep(0.05)", tmp_path)
    p.start()
    wait_for(lambda: (tmp_path / "proc" / "ready").exists())
    t0 = time.monotonic()
    rep = p.stop(grace_s=0.4, kill_after_s=3)
    assert rep["escalated_to_sigkill"] is True and rep["killed_by_signal"] == "SIGKILL" and time.monotonic() - t0 < 5


def test_the_child_leads_its_own_process_group_so_stop_cannot_signal_the_parent(tmp_path):
    p = _child("import time\nopen('ready','w').write('1')\nwhile True: time.sleep(0.05)", tmp_path)
    pid = p.start()
    try:
        wait_for(lambda: (tmp_path / "proc" / "ready").exists())
        assert os.getpgid(pid) == pid != os.getpgid(0)
    finally:
        p.stop(grace_s=2)
    assert os.getpgid(0) != pid


def test_a_fatal_signal_is_reported_and_faulthandler_leaves_the_python_stack(tmp_path):
    fault = tmp_path / "proc" / "fault.log"
    script = (f"import faulthandler,resource,threading,time\nresource.setrlimit(resource.RLIMIT_CORE,(0,0))\n"
              f"faulthandler.enable(file=open({str(fault)!r},'w'),all_threads=True)\n"
              "threading.Thread(target=lambda: time.sleep(5), name='enigma-telemetry', daemon=True).start()\ntime.sleep(0.1)\nfaulthandler._sigsegv()")
    (tmp_path / "proc").mkdir()
    p = _child(script, tmp_path)
    p.start()
    wait_for(lambda: p.poll() is not None)
    st = p.status()
    assert st["returncode"] == -signal.SIGSEGV and st["killed_by_signal"] == "SIGSEGV"
    text = fault.read_text()
    assert "Fatal Python error: Segmentation fault" in text
    assert text.count("Thread 0x") + text.count("Current thread 0x") >= 2, "the stacks of ALL threads must be on disk, not just the crashing one"
    p.stop(grace_s=1)


def test_the_runtime_key_is_never_on_the_command_line_in_a_repr_or_in_status(tmp_path):
    p = ConnectorProcess(["--stage", "register"], {**os.environ, "ENIGMA_RUNTIME_TOKEN": TOKEN}, tmp_path / "proc",
                         command=[sys.executable, "-c", "import time\nopen('ready','w').write('1')\nwhile True: time.sleep(0.05)"])
    pid = p.start()
    try:
        wait_for(lambda: (tmp_path / "proc" / "ready").exists())
        assert TOKEN.encode() not in Path(f"/proc/{pid}/cmdline").read_bytes()
        assert TOKEN not in repr(p) and TOKEN not in json.dumps(p.status())
    finally:
        p.stop(grace_s=2)


# --------------------------------------------------------------------------------------------- against the real API (live local server, mock mode)
def test_register_stage_as_a_subprocess_against_the_live_api_labels_everything_mock_and_leaks_nothing(live, tenant, tmp_path):
    env = {**os.environ, "PYTHONPATH": CONNECTOR_DIR, "ENIGMA_API_URL": live.url, "ENIGMA_RUNTIME_TOKEN": tenant.token, "ENIGMA_MODE": "mock"}
    p = ConnectorProcess(["--stage", "register", "--mode", "mock"], env, tmp_path / "proc")
    p.start()
    assert wait_for(lambda: p.poll() is not None) is not None
    st = p.status()
    assert st["returncode"] == 0, p.tail("stderr")
    summary = json.loads((tmp_path / "proc" / "connector.stdout.log").read_text().strip().splitlines()[-1])
    assert summary["mock"] is True and summary["stage"] == "register" and summary["runtime_id"] and summary["exit_code"] == 0
    events = [json.loads(line)["event"] for line in (tmp_path / "proc" / "connector.diag.jsonl").read_text().splitlines()]
    assert events[0] == "process_start" and "register_ok" in events and "thread_started" not in events and events[-1] == "process_exit"
    for f in (tmp_path / "proc").iterdir():
        assert tenant.token not in f.read_text(errors="replace"), f.name
    p.stop(grace_s=1)


def test_a_real_connector_cannot_register_with_a_mock_runtime_credential(live, tenant, cfg, api, tmp_path):
    cfg.mode = "stokastisk"
    c = Connector(cfg, api, MockLlm(0.001, 0.001), None, None, Spool(str(tmp_path / "s")), is_mock=False)            # is_mock=False => a REAL connector
    with pytest.raises(ApiError) as e:
        c.start()
    assert e.value.status == 409 and e.value.code == "mock_mismatch" and c._threads == []


# --------------------------------------------------------------------------------------------- the notebook-facing stage runner
from enigma_connector.stage_runner import evaluate, run_stage, system_snapshot  # noqa: E402


def quiet(*a, **k):
    pass


def test_run_stage_register_passes_against_the_live_api_and_keeps_the_key_out_of_everything(live, tenant, tmp_path):
    res = run_stage("register", api_url=live.url, token_getter=lambda: tenant.token, mode="mock", workdir=tmp_path, poll_s=0.2, echo=quiet)
    assert res["passed"], res["checks"]
    assert res["summary"]["mock"] is True and res["summary"]["runtime_id"]
    blob = json.dumps(res, default=str) + "".join(f.read_text(errors="replace") for f in Path(res["dir"]).iterdir())
    assert tenant.token not in blob


def test_run_stage_telemetry_passes_with_heartbeats_and_uploads_and_never_claims_jobs(live, tenant, tmp_path):
    res = run_stage("telemetry", api_url=live.url, token_getter=lambda: tenant.token, mode="mock", duration=4, workdir=tmp_path, poll_s=0.3, echo=quiet,
                    python_env={"ENIGMA_HEARTBEAT_S": "1"}, extra_args=("--gpu-interval", "0.2", "--vllm-interval", "0.4", "--upload-interval", "0.8"))   # well under the API's 5 req/s per-runtime limit
    assert res["passed"], [c for c in res["checks"] if not c[1]]
    ev = [json.loads(line) for line in (Path(res["dir"]) / "connector.diag.jsonl").read_text().splitlines()]
    assert not any(e["event"] == "claim_ok" for e in ev) and sum(e["event"] == "upload_ok" for e in ev) >= 2


def test_run_stage_reports_the_failing_component_instead_of_hiding_it(tmp_path):
    msgs = []
    res = run_stage("vllm", vllm_url="http://127.0.0.1:9", duration=1.5, workdir=tmp_path, poll_s=0.2, echo=lambda *a: msgs.append(" ".join(map(str, a))))
    assert res["passed"] is False
    failed = [name for name, ok, _ in res["checks"] if not ok]
    assert "every scrape parsed" in failed and any("FAIL" in m for m in msgs)


def test_run_stage_nvml_is_refused_on_a_no_gpu_host_and_says_so(tmp_path):
    res = run_stage("nvml", duration=1, workdir=tmp_path, poll_s=0.2, echo=quiet, python_env={"ENIGMA_FORBID_LOCAL_GPU": "1"})
    assert res["passed"] is False and res["exit"]["returncode"] == cli.EXIT_REFUSED


def test_run_stage_rejects_unknown_stages_and_network_stages_without_credentials(tmp_path):
    with pytest.raises(ValueError):
        run_stage("warp", workdir=tmp_path)
    with pytest.raises(ValueError):
        run_stage("heartbeat", workdir=tmp_path)


def test_evaluate_names_the_last_event_when_a_process_dies_mid_run():
    ev = [{"event": "process_start"}, {"event": "register_ok"}, {"event": "thread_started", "name": "heartbeat"}, {"event": "heartbeat_ok"}, {"event": "claim_ok"}]
    checks = evaluate("heartbeat", ev, None, {"returncode": -signal.SIGSEGV, "killed_by_signal": "SIGSEGV", "uptime_s": 9.7}, 60)
    by = {n: (ok, d) for n, ok, d in checks}
    assert by["child exited by itself with code 0"][0] is False and "SIGSEGV" in by["child exited by itself with code 0"][1]
    assert by["diagnostic log reached 'process_exit'"] == (False, "last_event=claim_ok")


def test_system_snapshot_reads_memory_and_the_oom_kill_counter():
    files = {"/proc/meminfo": "MemTotal: 1 kB\nMemAvailable:   2097152 kB\n", "/sys/fs/cgroup/memory.current": "1073741824\n", "/sys/fs/cgroup/memory.max": "max\n",
             "/sys/fs/cgroup/memory.events": "low 0\nhigh 0\nmax 0\noom 1\noom_kill 3\n"}
    s = system_snapshot(read=files.get)
    assert s == {"mem_available_mib": 2048.0, "cgroup_current_mib": 1024.0, "cgroup_limit_mib": None, "oom_kill": 3}
    assert system_snapshot(read=lambda p: None)["oom_kill"] is None
