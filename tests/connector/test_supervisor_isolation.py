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
