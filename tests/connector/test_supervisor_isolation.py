"""CPU-only regression tests for the supervised-subprocess lifecycle (Kaggle fatal-session investigation, 2026-11-26).

What must hold, whatever happens to the child:
* a child that exits BY ITSELF is never signalled afterwards (no SIGTERM/SIGKILL to a reaped PID or its old group);
* the supervisor never signals the notebook's own process group/session, and never a process that is not its own session leader;
* the runtime key never reaches the marker file, the diag log, stdout or stderr;
* every lifecycle marker is written (flushed + fsynced) in order;
* omitted offline sampling intervals fall back to the Config defaults instead of raising TypeError.
No GPU, NVML, CUDA or network to Enigma is used anywhere in this file."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from enigma_connector import cli
from enigma_connector.config import Config
from enigma_connector.stage_runner import MarkerLog, process_identity, run_stage
from enigma_connector.supervise import ConnectorProcess

TOKEN = "enig_rt_abcdefgh_" + "B" * 43


def quiet(*a, **k):
    pass


def wait_for(fn, timeout=8.0, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condition not met in time")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:                                                     # a zombie still answers kill(0); treat it as dead
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


@pytest.fixture
def sentinel():
    """An unrelated long-lived process in ITS OWN session: if the supervisor ever signalled a foreign group, this would die."""
    s = subprocess.Popen([sys.executable, "-c", "import time\nwhile True: time.sleep(0.1)"], start_new_session=True)
    yield s
    s.kill()
    s.wait(5)


# ------------------------------------------------------------------------------------------------ interval defect (NoneType)
@pytest.mark.parametrize("stage,attr", [("nvml", "gpu_interval_s"), ("vllm", "vllm_interval_s")])
def test_omitted_offline_interval_falls_back_to_the_config_default(stage, attr):
    a = argparse.Namespace(gpu_interval=None, vllm_interval=None)
    assert cli.offline_interval(stage, a) == Config.__dataclass_fields__[attr].default > 0


def test_explicit_offline_intervals_are_preserved_and_nonpositive_ones_rejected():
    assert cli.offline_interval("nvml", argparse.Namespace(gpu_interval=1.0, vllm_interval=None)) == 1.0
    assert cli.offline_interval("vllm", argparse.Namespace(gpu_interval=None, vllm_interval=2.0)) == 2.0
    with pytest.raises(ValueError):
        cli.offline_interval("vllm", argparse.Namespace(gpu_interval=None, vllm_interval=0.0))


def test_vllm_stage_without_interval_flags_no_longer_raises_typeerror(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ENIGMA_VLLM_URL", "http://127.0.0.1:9")                  # closed port: scrapes fail, the loop must still run
    rc = cli.main(["--stage", "vllm", "--duration", "0.3", "--diag-log", str(tmp_path / "d.jsonl")])
    assert rc == cli.EXIT_OK
    ev = [json.loads(x) for x in (tmp_path / "d.jsonl").read_text().splitlines()]
    assert ev[-1]["event"] == "process_exit" and any(e["event"] == "vllm_scrape" for e in ev)


def test_vllm_stage_with_a_zero_interval_is_a_config_error_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("ENIGMA_VLLM_URL", "http://127.0.0.1:9")
    assert cli.main(["--stage", "vllm", "--duration", "0.2", "--vllm-interval", "0"]) == cli.EXIT_CONFIG


# ------------------------------------------------------------------------------------------------ cleanup after a normal exit
def test_a_child_that_exited_by_itself_is_never_signalled_and_nothing_else_is_touched(tmp_path, sentinel):
    p = ConnectorProcess([], dict(os.environ), tmp_path / "proc", command=[sys.executable, "-c", "print('done')"])
    me = process_identity()
    pid = p.start()
    wait_for(lambda: p.poll() is not None)
    rep = p.stop(grace_s=1, kill_after_s=1)
    assert rep["returncode"] == 0 and rep["killed_by_signal"] is None
    assert rep["signals_sent"] == [] and rep["signals_refused"] == [] and rep["escalated_to_sigkill"] is False
    assert process_identity() == me                                                   # the notebook-side process is untouched
    assert _alive(sentinel.pid) and not _alive(pid)
    rep2 = p.stop(grace_s=1)                                                           # a second cleanup is a no-op as well
    assert rep2["signals_sent"] == []


def test_signal_is_refused_for_a_pid_that_is_not_our_session_leader(tmp_path):
    """Regression for the removed ``os.kill(pid)`` fallback: a PID that does not lead its own session can only be a DIFFERENT process."""
    victim = subprocess.Popen([sys.executable, "-c", "import time\nwhile True: time.sleep(0.1)"])     # same group/session as the test: not ours to kill
    try:
        p = ConnectorProcess([], {}, tmp_path / "proc")
        p.proc = victim                                                                 # simulate "our child's PID now names another process"
        assert p._signal(signal.SIGTERM) is False
        assert p.refused_signals and p.refused_signals[0]["reason"] == "pid_is_not_our_session_leader"
        time.sleep(0.2)
        assert victim.poll() is None, "an unrelated process was signalled"
    finally:
        victim.kill()
        victim.wait(5)


def test_signal_is_refused_for_the_notebooks_own_group(tmp_path):
    class Me:                                                                            # a 'child' whose PID is this very process
        pid = os.getpid()

        def poll(self):
            return None
    p = ConnectorProcess([], {}, tmp_path / "proc")
    p.proc = Me()
    assert p._signal(signal.SIGTERM) is False                                             # still alive to assert this line = not signalled


def test_a_reaped_child_is_not_signalled_even_if_stop_races_its_exit(tmp_path, sentinel):
    p = ConnectorProcess([], dict(os.environ), tmp_path / "proc", command=[sys.executable, "-c", "import time; time.sleep(0.2)"])
    p.start()
    p.proc.wait(5)                                                                         # reaped: from here its PID may be reused by the OS
    assert p._signal(signal.SIGKILL) is False and p.sent_signals == []
    assert _alive(sentinel.pid)


def test_sigterm_cleanup_reaches_only_the_childs_group_including_its_grandchild(tmp_path, sentinel):
    script = ("import subprocess,sys,time\n"
              "g=subprocess.Popen([sys.executable,'-c','import time\\nwhile True: time.sleep(0.1)'])\n"
              "open('grandchild','w').write(str(g.pid))\nwhile True: time.sleep(0.1)")
    p = ConnectorProcess([], dict(os.environ), tmp_path / "proc", command=[sys.executable, "-c", script])
    p.start()
    gpid = int(wait_for(lambda: (tmp_path / "proc" / "grandchild").exists() and (tmp_path / "proc" / "grandchild").read_text()))
    rep = p.stop(grace_s=3, kill_after_s=2)
    assert rep["signals_sent"][0] == "SIGTERM" and rep["returncode"] in (-signal.SIGTERM, -signal.SIGKILL)
    wait_for(lambda: not _alive(gpid))
    assert _alive(sentinel.pid)


def test_failed_spawn_closes_log_handles_and_drops_the_key(tmp_path):
    def boom(*a, **k):
        raise OSError("spawn failed")
    p = ConnectorProcess([], {"ENIGMA_RUNTIME_TOKEN": TOKEN}, tmp_path / "proc", popen=boom)
    with pytest.raises(OSError):
        p.start()
    assert p._fhs == [] and p._env == {}


# ------------------------------------------------------------------------------------------------ markers / evidence
EXPECTED_ORDER = ["stage_begin", "before_secret_retrieval", "after_secret_retrieval", "before_spawn", "after_spawn",
                  "before_supervisor_cleanup", "after_supervisor_cleanup", "before_stage_return"]


def test_network_stage_markers_are_complete_ordered_and_secret_free(tmp_path):
    """Registration against a CLOSED local port in MOCK mode: no Enigma, no GPU. The child retries, the supervisor's grace expires, SIGTERM cleans up."""
    echoed: list[str] = []
    res = run_stage("register", api_url="http://127.0.0.1:9", token_getter=lambda: TOKEN, mode="mock", workdir=tmp_path, poll_s=0.2, grace_s=1.5,
                    echo=lambda *a, **k: echoed.append(" ".join(map(str, a))))
    assert res["passed"] is False                                                           # it never registered: honest failure
    marks = [json.loads(x) for x in (tmp_path / "markers.jsonl").read_text().splitlines()]
    assert [m["marker"] for m in marks] == EXPECTED_ORDER
    assert all(m["self"]["pid"] == os.getpid() for m in marks)
    spawn = next(m for m in marks if m["marker"] == "after_spawn")
    assert spawn["child"]["pgid"] == spawn["child"]["sid"] == spawn["child"]["pid"] != spawn["self"]["pgid"]
    clean = next(m for m in marks if m["marker"] == "after_supervisor_cleanup")
    assert clean["signals_sent"][:1] == ["SIGTERM"] and clean["signals_refused"] == []
    ev = [json.loads(x) for x in (Path(res["dir"]) / "connector.diag.jsonl").read_text().splitlines()]
    assert any(e["event"] == "register_request_begin" for e in ev)
    blobs = [(tmp_path / "markers.jsonl").read_text(), *(f.read_text(errors="replace") for f in Path(res["dir"]).iterdir()), "\n".join(echoed),
             json.dumps(res, default=str)]
    assert not any(TOKEN in b or TOKEN[-43:] in b for b in blobs)
    assert sum(line.startswith("MARK ") for line in echoed) == len(EXPECTED_ORDER)


def test_offline_stage_marks_the_childs_own_exit_and_sends_no_signal(tmp_path):
    res = run_stage("vllm", vllm_url="http://127.0.0.1:9", duration=0.6, workdir=tmp_path, poll_s=0.2, echo=quiet)
    marks = [json.loads(x) for x in (tmp_path / "markers.jsonl").read_text().splitlines()]
    names = [m["marker"] for m in marks]
    assert "before_secret_retrieval" not in names and "child_exit_observed" in names
    clean = next(m for m in marks if m["marker"] == "after_supervisor_cleanup")
    assert clean["child_exit_seen_before_cleanup"] is True and clean["signals_sent"] == [] and clean["returncode"] == 0
    assert res["exit"]["signals_sent"] == []


def test_marker_log_survives_an_unwritable_path_and_redacts(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    out: list[str] = []
    rec = MarkerLog(blocker / "sub" / "markers.jsonl", echo=lambda *a, **k: out.append(a[0])).mark("x", note=f"token={TOKEN}")
    assert TOKEN not in json.dumps(rec) and out and TOKEN not in out[0]


# ------------------------------------------------------------------------------------------------ finite interval / duration validation (review 2026-11-26)
import math                                                                          # noqa: E402
import threading                                                                     # noqa: E402

from enigma_connector.config import INTERVAL_LIMITS, ConfigError, finite_number      # noqa: E402
from enigma_connector.probe import ProbeWorkerError, run_local_nvml, run_local_vllm  # noqa: E402
from enigma_connector.diag import DiagLog                                            # noqa: E402
from enigma_connector.stage_runner import evaluate                                   # noqa: E402

BAD = [0.0, -1.0, math.nan, math.inf, -math.inf, "abc", True, None]


def _cfg(**over):
    return Config(api_url="https://api.test", token=TOKEN, instance_id="t", **over)


def test_notebook_intervals_and_defaults_are_valid():
    c = _cfg(gpu_interval_s=1.0, vllm_interval_s=2.0, upload_interval_s=5.0).validate()
    assert (c.gpu_interval_s, c.vllm_interval_s, c.upload_interval_s, c.heartbeat_interval_s) == (1.0, 2.0, 5.0, 10.0)


@pytest.mark.parametrize("attr", [a for a, *_ in INTERVAL_LIMITS])
@pytest.mark.parametrize("bad", [v for v in BAD if v is not None])
def test_every_interval_rejects_nonfinite_nonpositive_and_non_numbers(attr, bad):
    lo_ok = next(ok for a, _, _, ok in INTERVAL_LIMITS if a == attr)
    if lo_ok and bad == 0.0:
        pytest.skip(f"{attr}: 0 is a documented, allowed value")
    with pytest.raises(ConfigError):
        _cfg(**{attr: bad}).validate()


def test_allowed_zero_values_keep_their_meaning():
    c = _cfg(claim_poll_idle_s=0.0, claim_hot_window_s=0.0).validate()
    assert c.claim_poll_idle_s == 0.0 and c.claim_hot_window_s == 0.0


@pytest.mark.parametrize("raw", ["nan", "inf", "-5", "0", "x"])
def test_environment_heartbeat_must_be_finite_positive(raw):
    with pytest.raises(ConfigError):
        Config.from_env({"ENIGMA_API_URL": "https://api.test", "ENIGMA_RUNTIME_TOKEN": TOKEN, "ENIGMA_INSTANCE": "t", "ENIGMA_HEARTBEAT_S": raw})


@pytest.mark.parametrize("stage,flag", [("nvml", "gpu_interval"), ("vllm", "vllm_interval")])
@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf])
def test_offline_interval_rejects_zero_negative_nan_and_infinity(stage, flag, bad):
    a = argparse.Namespace(gpu_interval=None, vllm_interval=None)
    setattr(a, flag, bad)
    with pytest.raises(ValueError):
        cli.offline_interval(stage, a)


@pytest.mark.parametrize("args", [["--duration", "inf"], ["--duration", "nan"], ["--duration", "-1"], ["--hang-dump-s", "inf"],
                                  ["--gpu-interval", "inf"], ["--vllm-interval", "nan"], ["--upload-interval", "0"]])
def test_cli_rejects_bad_durations_and_intervals_before_doing_anything(args, tmp_path):
    assert cli.main(["--stage", "vllm", "--diag-log", str(tmp_path / "d.jsonl"), *args]) == cli.EXIT_CONFIG
    assert not (tmp_path / "d.jsonl").exists()                                       # refused before the diag log was even opened


def test_zero_duration_still_means_register_only_or_indefinite():
    assert finite_number("--duration", 0.0, maximum=86400.0, allow_minimum=True) == 0.0


def test_run_stage_rejects_nonfinite_duration_poll_and_grace(tmp_path):
    for kw in ({"duration": math.inf}, {"duration": math.nan}, {"poll_s": 0}, {"poll_s": math.inf}, {"grace_s": -1}):
        with pytest.raises(ValueError):
            run_stage("vllm", workdir=tmp_path, echo=quiet, **kw)


# ------------------------------------------------------------------------------------------------ probe worker failures must not look like success
class ExplodingSampler:
    last_error = None

    def sample(self):
        raise RuntimeError("worker blew up")

    def inventory(self):
        return []


def test_vllm_probe_worker_crash_is_raised_and_logged(tmp_path):
    d = DiagLog(tmp_path / "p.jsonl")
    with pytest.raises(ProbeWorkerError) as e:
        run_local_vllm(ExplodingSampler(), d, threading.Event(), interval_s=0.01, duration_s=0.2)
    assert e.value.error_type == "RuntimeError"
    ev = [json.loads(x) for x in (tmp_path / "p.jsonl").read_text().splitlines()]
    assert any(x["event"] == "probe_worker_crashed" and x["error"] == "RuntimeError" for x in ev)
    assert not any(x["event"] == "vllm_probe_done" for x in ev)


def test_nvml_probe_worker_crash_outside_the_sample_call_is_raised(tmp_path):
    class BadDiag(DiagLog):
        def emit(self, event, **f):
            if event == "nvml_sample":
                raise OSError("diag write path exploded")
            super().emit(event, **f)

    class OkNvml:
        def inventory(self):
            return []

        def sample(self):
            return []
    with pytest.raises(ProbeWorkerError):
        run_local_nvml(OkNvml(), BadDiag(tmp_path / "n.jsonl"), threading.Event(), interval_s=0.01, duration_s=0.2)


def test_cli_returns_nonzero_and_logs_the_same_code_when_the_probe_worker_crashes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "VllmSampler", lambda url: ExplodingSampler())
    monkeypatch.setenv("ENIGMA_VLLM_URL", "http://127.0.0.1:9")
    rc = cli.main(["--stage", "vllm", "--duration", "0.3", "--diag-log", str(tmp_path / "d.jsonl")])
    assert rc == cli.EXIT_FATAL
    ev = [json.loads(x) for x in (tmp_path / "d.jsonl").read_text().splitlines()]
    assert ev[-1]["event"] == "process_exit" and ev[-1]["code"] == cli.EXIT_FATAL
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["exit_code"] == cli.EXIT_FATAL and summary["fatal"] == "RuntimeError"


def test_process_exit_is_never_logged_as_zero_when_main_raises(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(cli, "_offline", boom)
    with pytest.raises(RuntimeError):
        cli.main(["--stage", "vllm", "--duration", "0.1", "--diag-log", str(tmp_path / "d.jsonl")])
    ev = [json.loads(x) for x in (tmp_path / "d.jsonl").read_text().splitlines()]
    assert ev[-1] == {**ev[-1], "event": "process_exit", "code": cli.EXIT_FATAL}


def test_evaluate_fails_when_the_logged_exit_code_disagrees_with_reality():
    ev = [{"event": "process_start"}, {"event": "vllm_scrape", "got": True}, {"event": "process_exit", "code": 0}]
    checks = {n: ok for n, ok, _ in evaluate("vllm", ev, {"stage": "vllm", "exit_code": 0}, {"returncode": 1}, 0.0)}
    assert checks["logged exit code matches the real exit code"] is False and checks["child exited by itself with code 0"] is False


def test_evaluate_fails_an_offline_stage_whose_worker_crashed():
    ev = [{"event": "process_start"}, {"event": "probe_worker_crashed", "error": "TypeError"}, {"event": "process_exit", "code": 1}]
    checks = {n: ok for n, ok, _ in evaluate("vllm", ev, None, {"returncode": 1}, 0.0)}
    assert checks["probe worker did not crash"] is False


def test_run_stage_end_to_end_reports_a_crashing_probe_as_fail(tmp_path):
    """The old (pre-fix) symptom: TypeError in the probe thread while the child still exited 0. Now the stage itself FAILS."""
    res = run_stage("vllm", vllm_url="http://127.0.0.1:9", duration=0.5, workdir=tmp_path, poll_s=0.2, echo=quiet, extra_args=("--vllm-interval", "nan"))
    assert res["passed"] is False and res["exit"]["returncode"] == cli.EXIT_CONFIG


# ------------------------------------------------------------------------------------------------ concurrent stop()
def test_concurrent_stop_calls_send_exactly_one_sigterm_and_harm_nothing(tmp_path, sentinel):
    p = ConnectorProcess([], dict(os.environ), tmp_path / "proc", command=[sys.executable, "-c", "import time\nwhile True: time.sleep(0.05)"])
    p.start()
    time.sleep(0.3)
    reports: list[dict] = []
    threads = [threading.Thread(target=lambda: reports.append(p.stop(grace_s=5, kill_after_s=2))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert len(reports) == 4 and p.sent_signals == ["SIGTERM"] and p.refused_signals == []
    assert all(r["returncode"] == -signal.SIGTERM for r in reports)
    assert _alive(sentinel.pid)
