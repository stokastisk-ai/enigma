"""External orchestration of the immutable build-5 ServerProcess (CPU only; a FAKE server object with the build-5 attribute shape, no vLLM)."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

from enigma_connector import serverctl


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


class FakeBuild5Server:
    """Same attributes build-5 ServerProcess uses (_proc, _pgid, _log, shutdown_report, stop()). stop() records that it was called."""

    def __init__(self, script: str):
        self._proc = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
        self._pgid = self._proc.pid
        self._log = open(os.devnull, "w")
        self.shutdown_report: dict = {}
        self.stop_calls = 0

    @property
    def pid(self):
        return None if self._proc is None else self._proc.pid

    def stop(self, grace=30.0):
        if self._proc is None:                               # build 5: stop() without a process is a no-op
            return None
        self.stop_calls += 1
        os.killpg(self._pgid, signal.SIGTERM)
        rc = self._proc.wait(10)
        self.shutdown_report = {"signals": ["SIGTERM"], "group_gone": True}
        self._proc = None
        return rc


CHILD = "import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c','import time\\ntime.sleep(60)'])\ntime.sleep(60)"


def sentinel():
    return subprocess.Popen([sys.executable, "-c", "import time\nwhile True: time.sleep(0.1)"], start_new_session=True)


def test_running_server_uses_the_package_stop():
    s = FakeBuild5Server(CHILD)
    time.sleep(0.3)
    r = serverctl.safe_stop(s)
    assert r["action"] == "package_stop" and s.stop_calls == 1


def test_a_server_whose_leader_already_exited_is_never_stopped_through_the_package_and_orphans_are_killed():
    s = FakeBuild5Server(CHILD)
    time.sleep(0.5)
    pgid = s._pgid
    os.kill(s._proc.pid, signal.SIGKILL)
    s._proc.wait(5)                                          # leader reaped: build-5 stop() would now be unsafe
    guard = sentinel()
    try:
        assert serverctl.group_members(pgid)                 # the engine-core-like child survives the leader
        r = serverctl.safe_stop(s)
        assert r["action"] == "leader_already_exited" and s.stop_calls == 0
        assert r["orphans"]["signalled"] == 1 and r["orphans"]["refused"] == 0
        deadline = time.time() + 5
        while serverctl.group_members(pgid) and time.time() < deadline:
            time.sleep(0.05)
        assert serverctl.group_members(pgid) == [] and alive(guard.pid)
        assert s._proc is None and s._pgid is None and s.stop() is None and s.stop_calls == 0     # neutralised: a later stop() cannot probe the old group
    finally:
        guard.kill(); guard.wait(5)


def test_a_process_holding_the_pgid_number_is_never_a_member():
    guard = sentinel()                                       # (pgid, sid) == (pid, pid): exactly what a reused PGID looks like
    try:
        assert guard.pid not in serverctl.group_members(guard.pid)
        assert serverctl.kill_verified_orphans(guard.pid) == {"signalled": 0, "refused": 0, "pidfd": 1}
        time.sleep(0.2)
        assert alive(guard.pid)
    finally:
        guard.kill(); guard.wait(5)


def test_safe_stop_twice_is_a_noop():
    s = FakeBuild5Server("import time; time.sleep(60)")
    serverctl.safe_stop(s)
    assert serverctl.safe_stop(s)["action"] == "none"


def test_prepare_loopback_env_sets_no_proxy_once():
    env = {"no_proxy": "corp"}
    out = serverctl.prepare_loopback_env(env)
    serverctl.prepare_loopback_env(env)
    assert out["no_proxy"] == env["no_proxy"] == "corp,127.0.0.1,localhost,::1" and env["NO_PROXY"] == "127.0.0.1,localhost,::1"
