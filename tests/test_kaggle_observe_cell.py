"""CPU-only tests of the EXACT survival-observation code inlined into the notebooks (scripts/kaggle_cells/observe.py), on a fake clock."""
from __future__ import annotations

from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parents[1] / "scripts" / "kaggle_cells" / "observe.py").read_text()


class Stop(Exception):
    pass


class FakeTime:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        assert s >= 0
        self.now += s


@pytest.fixture
def nb():
    ns: dict = {}
    clock, log, remote, verdicts = FakeTime(), [], [], {}
    state = {"oom": {"oom_kill": 0}, "ack": True, "die_at": None}

    def mark(name, **f):
        if state["die_at"] is not None and clock.now >= state["die_at"]:
            raise SystemExit("VM terminated")                       # nothing after this point is recorded anywhere
        log.append((round(clock.now, 2), name, f))
        return {"utc": "x", "t": clock.now}

    class Res(dict):
        def __bool__(self):
            return self.get("code") == "DIAGNOSTIC_PERSISTENCE_VERIFIED"

    def rmark(name, **f):
        mark(name, **f)
        remote.append((round(clock.now, 2), name, f))
        return Res(code="DIAGNOSTIC_PERSISTENCE_VERIFIED" if state["ack"] else "DIAGNOSTIC_HTTP_ERROR")

    def rcheck(step, name, **f):
        r = rmark(name, **f)
        if not r:
            verdict(step, "INCONCLUSIVE", "not durable")
        return r

    def verdict(step, status, reason, **ev):
        verdicts[step] = (status, ev)
        if status != "PASS":
            raise Stop(step)

    ns.update(time=clock, mark=mark, rmark=rmark, rcheck=rcheck, verdict=verdict, host=lambda: {"cgroup_events": dict(state["oom"])})
    exec(compile(SRC, "observe.py", "exec"), ns)
    ns.update(_log=log, _remote=remote, _verdicts=verdicts, _state=state, _clock=clock)
    return ns


def names(nb):
    return [n for _, n, _ in nb["_log"]]


def test_schedule_is_dense_first_then_sparse_and_ends_exactly():
    ns: dict = {}
    exec(compile(SRC, "observe.py", "exec"), ns)
    s = ns["tick_schedule"](70, 15, dense_first_s=10, dense_every=2)
    assert s[:5] == [2, 4, 6, 8, 10] and s[5:] == [25, 40, 55, 70] and s[-1] == 70


def test_passive_window_sends_nothing_and_brackets_every_sleep(nb):
    nb["observe"]("w", 60, every=2, dense_first_s=60, dense_every=2)
    assert nb["_remote"] == []
    n = names(nb)
    assert n[0] == "w_begin" and n[-1] == "w_complete"
    assert n.count("w_sleep_begin") == n.count("w_sleep_end") == 30
    assert nb["_verdicts"]["w"][0] == "PASS" and nb["_verdicts"]["w"][1]["remote_sent"] == 0


def test_active_window_sends_only_at_the_requested_offsets_with_local_brackets(nb):
    nb["observe"]("w", 60, every=5, remote_at=(5, 15, 30, 60))
    assert [t for t, _, _ in nb["_remote"]] == [5, 15, 30, 60]
    n = names(nb)
    assert n.count("w_remote_submit") == n.count("w_remote_result") == 4
    i = n.index("w_remote_submit")
    assert n[i - 1] == "w_sleep_end" and n[i + 1] == "w_alive" and n[i + 2] == "w_remote_result"
    assert nb["_verdicts"]["w"][1]["remote_acknowledged"] == 4


def test_durable_observation_start_comes_before_the_first_sleep(nb):
    nb["observe"]("w", 30, every=15, remote_at=(30,), durable_begin=True)
    assert nb["_remote"][0][1] == "w_obs_begin" and nb["_remote"][0][0] == 0
    n = names(nb)
    assert n.index("w_obs_begin") < n.index("w_begin") < n.index("w_sleep_begin")


def test_a_failed_durable_start_stops_before_the_window(nb):
    nb["_state"]["ack"] = False
    with pytest.raises(Stop):
        nb["observe"]("w", 30, durable_begin=True)
    assert "w_begin" not in names(nb)


def test_unacknowledged_remote_marker_fails_the_window(nb):
    nb["_state"]["ack"] = False
    with pytest.raises(Stop):
        nb["observe"]("w", 30, every=15, remote_at=(15,))
    assert nb["_verdicts"]["w"][0] == "FAIL"


def test_interruption_leaves_the_last_local_tick_and_no_verdict(nb):
    nb["_state"]["die_at"] = 13.0                                       # a VM termination 13 s into a passive 2-s-tick window
    with pytest.raises(SystemExit):
        nb["observe"]("w", 60, every=2, dense_first_s=60, dense_every=2)
    last_t, last_name, _ = nb["_log"][-1]
    assert last_name in ("w_sleep_begin", "w_sleep_end") and 12 <= last_t < 13
    assert "w" not in nb["_verdicts"] and nb["_remote"] == []           # passive: the death cannot be attributed to a diagnostic request


def test_missing_oom_counter_is_unavailable_not_zero(nb):
    nb["_state"]["oom"] = {}
    nb["observe"]("w", 15, every=15)
    assert nb["_verdicts"]["w"][1]["oom_kill_delta"] == "unavailable"
    assert nb["_log"][0][2]["oom_state"] == "unavailable"


def test_an_oom_kill_during_the_window_fails_it(nb):
    def bump():
        nb["_state"]["oom"] = {"oom_kill": 1}
        return {}
    with pytest.raises(Stop):
        nb["observe"]("w", 15, every=15, sample=bump)
    assert nb["_verdicts"]["w"][1]["oom_kill_delta"] == 1


def test_remote_markers_are_bounded(nb):
    with pytest.raises(AssertionError):
        nb["observe"]("w", 600, remote_at=tuple(range(10, 140, 10)))
