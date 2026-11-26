"""Notebook-facing driver for the staged connector diagnostics. One call = one isolated stage, run as a SUPERVISED CHILD PROCESS, with explicit pass/fail checks.

    from enigma_connector.stage_runner import run_stage
    result = run_stage("register", api_url=..., token_getter=lambda: SECRET, vllm_url=server.base_url, model=MODEL, server=server)

It never prints or returns the runtime key, never starts the vLLM server, never installs anything, and always ends by stopping only its own child.
While it watches, it samples host memory and the cgroup OOM-kill counter, because a kernel killed by the OS leaves no Python traceback."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .cli import OFFLINE_STAGES, STAGES
from .diag import redact
from .supervise import ConnectorProcess

DEFAULT_DURATION = {"register": 0, "heartbeat": 60, "jobs": 60, "nvml": 120, "vllm": 60, "upload-once": 15, "telemetry": 120, "full": 600}
Check = tuple[str, bool, str]


# ------------------------------------------------------------------------------------------------ host evidence (read-only, any Linux)
def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read(65536)
    except OSError:
        return None


def system_snapshot(read: Callable[[str], str | None] = _read) -> dict[str, Any]:
    """Host memory and the cgroup OOM-kill counter. ``oom_kill`` increasing between two snapshots means the OS killed a process for memory."""
    mib = lambda b: None if b is None else round(b / 1048576, 1)                       # noqa: E731
    snap: dict[str, Any] = {}
    mi = read("/proc/meminfo") or ""
    for line in mi.splitlines():
        if line.startswith("MemAvailable:"):
            snap["mem_available_mib"] = round(int(line.split()[1]) / 1024, 1)
    cur, mx, ev = read("/sys/fs/cgroup/memory.current"), read("/sys/fs/cgroup/memory.max"), read("/sys/fs/cgroup/memory.events")
    if cur is None:                                                                      # cgroup v1 fallback
        cur, mx = read("/sys/fs/cgroup/memory/memory.usage_in_bytes"), read("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        ev = read("/sys/fs/cgroup/memory/memory.oom_control")
    to_int = lambda s: int(s.strip()) if s and s.strip().isdigit() else None            # noqa: E731  ("max" -> None = unlimited)
    snap["cgroup_current_mib"], snap["cgroup_limit_mib"] = mib(to_int(cur)), mib(to_int(mx))
    snap["oom_kill"] = None
    for line in (ev or "").splitlines():
        k, _, v = line.partition(" ")
        if k == "oom_kill" and v.strip().isdigit():
            snap["oom_kill"] = int(v)
    return snap


def process_identity(pid: int | None = None) -> dict[str, Any]:
    """PID, parent PID, process group and session of ``pid`` (default: this process). Never raises; unknown values are None."""
    out: dict[str, Any] = {"pid": os.getpid() if pid is None else pid}
    for key, fn in (("pgid", os.getpgid), ("sid", os.getsid)):
        try:
            out[key] = fn(0 if pid is None else pid)
        except OSError:
            out[key] = None
    if pid is None:
        out["ppid"] = os.getppid()
    return out


class MarkerLog:
    """Parent-side progress markers. Each marker is (1) appended to a JSONL file, flushed and fsynced, and (2) echoed as one flushed line.

    Why both: after the Kaggle fatal sessions the VM's files were gone (a new session had to re-clone), but the cell outputs printed before the
    failure WERE kept in the saved notebook. Markers carry host memory and process identities only; every value is redacted, no secret is ever passed."""

    def __init__(self, path: str | os.PathLike[str] | None, *, echo: Callable[..., None] | None = None, read: Callable[[str], str | None] = _read):
        self.path = None if path is None else Path(path)
        self._echo, self._read = echo, read
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.path = None                                                        # unwritable: markers still reach the cell output

    def mark(self, name: str, **fields: Any) -> dict[str, Any]:
        snap = system_snapshot(self._read)
        rec = redact({"marker": name, "t": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z",
                      "self": process_identity(), "mem_available_mib": snap.get("mem_available_mib"), "cgroup_mib": snap.get("cgroup_current_mib"),
                      "oom_kill": snap.get("oom_kill"), **fields})
        line = json.dumps(rec, separators=(",", ":"), default=str)
        if self.path is not None:
            try:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
            except OSError:
                pass                                                                    # evidence must never break the stage
        if self._echo is not None:
            try:
                self._echo("MARK " + line, flush=True)
            except TypeError:
                self._echo("MARK " + line)
        return rec


# ------------------------------------------------------------------------------------------------ evaluation of a finished stage
def _events(path: Path) -> list[dict]:
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass                                                                    # a torn last line after a hard kill is expected
    return out


def _count(ev: list[dict], name: str, **match: Any) -> int:
    return sum(1 for e in ev if e.get("event") == name and all(e.get(k) == v for k, v in match.items()))


def evaluate(stage: str, ev: list[dict], summary: dict | None, status: dict, duration: float, *, mode: str = "stokastisk") -> list[Check]:
    """Explicit pass/fail conditions. Every check names what it looked at, so a failure says which component and which last operation."""
    rc = status.get("returncode")
    checks: list[Check] = [("child exited by itself with code 0", rc == 0, f"returncode={rc} signal={status.get('killed_by_signal')} uptime_s={status.get('uptime_s')}"),
                           ("diagnostic log reached 'process_exit'", bool(ev) and ev[-1].get("event") == "process_exit", f"last_event={(ev[-1].get('event') if ev else None)}")]
    if stage in OFFLINE_STAGES:
        checks.append(("no network to Enigma was used", not any(e.get("event") in ("register_ok", "heartbeat_ok", "upload_ok", "claim_ok") for e in ev), "offline stage"))
    if stage == "nvml":
        n = _count(ev, "nvml_sample")
        checks += [(f"NVML sampled repeatedly from a background thread (>= {max(5, int(duration * 0.7))})", n >= max(5, int(duration * 0.7)), f"{n} samples"),
                   ("no NVML sample raised", _count(ev, "nvml_sample_error") == 0, f"{_count(ev, 'nvml_sample_error')} errors")]
    elif stage == "vllm":
        n, got = _count(ev, "vllm_scrape"), _count(ev, "vllm_scrape", got=True)
        checks += [("vLLM /metrics scraped repeatedly", n >= max(5, int(duration * 0.35)), f"{n} scrapes"), ("every scrape parsed", n > 0 and got == n, f"{got}/{n} parsed")]
    else:
        reg = _count(ev, "register_ok")
        checks += [("registered exactly once", reg == 1, f"{reg}x register_ok"),
                   (f"runtime is {'MOCK' if mode == 'mock' else 'REAL (is_mock=false)'}", summary is not None and summary.get("mock") is (mode == "mock"), f"summary.mock={None if summary is None else summary.get('mock')}")]
        started = {e.get("name") for e in ev if e.get("event") == "thread_started"}
        want = {"register": set(), "heartbeat": {"heartbeat"}, "jobs": {"jobs"}, "upload-once": {"telemetry"}, "telemetry": {"heartbeat", "telemetry"},
                "full": {"heartbeat", "telemetry", "jobs"}}[stage]
        checks.append(("only this stage's subsystems were started", started == want, f"started={sorted(started)} wanted={sorted(want)}"))
        no_fail = _count(ev, "heartbeat_failed") + _count(ev, "upload_transient_failure") + _count(ev, "upload_rejected") + _count(ev, "thread_crashed") == 0
        checks.append(("no heartbeat/upload failure and no thread crash", no_fail, "see diag log" if not no_fail else "clean"))
        if stage in ("heartbeat", "telemetry", "full"):
            hb = _count(ev, "heartbeat_ok")
            checks.append((f"heartbeats acknowledged (>= {max(2, int(duration / 12))})", hb >= max(2, int(duration / 12)), f"{hb} heartbeat_ok"))
        if stage in ("jobs", "full"):
            cl = _count(ev, "claim_ok")
            checks.append(("job polling acknowledged", cl >= max(3, int(duration / 6)), f"{cl} claim_ok"))
        if stage == "upload-once":
            up = _count(ev, "upload_ok")
            checks.append(("exactly one telemetry batch uploaded and acknowledged", up == 1, f"{up} upload_ok"))
        if stage in ("telemetry", "full"):
            up = _count(ev, "upload_ok")
            checks.append((f"telemetry batches acknowledged (>= {max(2, int(duration / 8))})", up >= max(2, int(duration / 8)), f"{up} upload_ok"))
        if stage in ("telemetry", "upload-once", "full"):
            g = _count(ev, "gpu_sample")
            checks.append(("GPU sampled from the connector's own thread", g >= 3, f"{g} gpu_sample"))
    return checks


# ------------------------------------------------------------------------------------------------ the driver
def run_stage(stage: str, *, api_url: str = "", token_getter: Callable[[], str] | None = None, vllm_url: str = "http://127.0.0.1:8000", model: str = "",
              tp: int = 1, max_model_len: int = 2048, duration: float | None = None, workdir: str | os.PathLike[str] = "/kaggle/working/enigma-diag",
              server: Any = None, mode: str = "stokastisk", extra_args: tuple[str, ...] = (), poll_s: float = 5.0, grace_s: float = 30.0,
              echo: Callable[..., None] = print, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
              python_env: dict[str, str] | None = None, markers: MarkerLog | None = None) -> dict[str, Any]:
    if stage not in {*STAGES, *OFFLINE_STAGES}:
        raise ValueError(f"unknown stage {stage!r}; choose one of {sorted({*STAGES, *OFFLINE_STAGES})}")
    dur = float(DEFAULT_DURATION[stage] if duration is None else duration)
    needs_net = stage not in OFFLINE_STAGES
    out_dir = Path(workdir) / f"{stage}-{uuid.uuid4().hex[:6]}"
    mk = markers if markers is not None else MarkerLog(Path(workdir) / "markers.jsonl", echo=echo)
    mk.mark("stage_begin", stage=stage, dir=out_dir.name)
    pkg_parent = str(Path(__file__).resolve().parents[1])                                   # the child must import THIS connector code, not whatever is on its default path
    env = {**os.environ, "ENIGMA_VLLM_URL": vllm_url, "ENIGMA_MODE": mode, "ENIGMA_INSTANCE": f"diag-{uuid.uuid4().hex[:10]}",
           "ENIGMA_CLAIM_POLL_S": "1", "ENIGMA_CLAIM_POLL_IDLE_S": "5", "ENIGMA_CLAIM_HOT_WINDOW_S": "30", "ENIGMA_HEARTBEAT_S": "10",
           **(python_env or {})}
    env["PYTHONPATH"] = os.pathsep.join(x for x in (pkg_parent, env.get("PYTHONPATH", "")) if x)
    if needs_net:
        if not api_url or token_getter is None:
            raise ValueError(f"stage {stage!r} talks to Enigma: api_url and token_getter are required")
        env["ENIGMA_API_URL"] = api_url
        mk.mark("before_secret_retrieval", stage=stage)
        env["ENIGMA_RUNTIME_TOKEN"] = token_getter().strip()                          # lives in this env dict only until the child has been spawned
        mk.mark("after_secret_retrieval", stage=stage, secret_present=bool(env["ENIGMA_RUNTIME_TOKEN"]))   # presence only: never the value or its length
    if model:
        env["ENIGMA_SERVED_MODEL"] = model
    args = ["--stage", stage, "--duration", str(dur), "--mode", mode, "--tp", str(tp), "--max-model-len", str(max_model_len), *extra_args]
    p = ConnectorProcess(args, env, out_dir)
    del env
    base = system_snapshot()
    echo(f"[stage {stage}] starting supervised child, duration={dur:g}s, logs in {out_dir}")
    mk.mark("before_spawn", stage=stage)
    pid = p.start()
    mk.mark("after_spawn", stage=stage, child=process_identity(pid))
    t0, timeline = clock(), []
    exit_marked = False
    try:
        while True:
            sleep(poll_s)
            el = clock() - t0
            st, last, snap = p.status(), p.last_event(), system_snapshot()
            srv = None if server is None else bool(getattr(server, "running", None))
            ev_name = None if not last else last.get("event")
            echo(f"  t+{el:5.0f}s child={'running' if st['running'] else 'EXITED rc=' + str(st['returncode']) + (' sig=' + st['killed_by_signal'] if st['killed_by_signal'] else '')}"
                 f" last={ev_name} seq={None if not last else last.get('seq')} vllm_server={'up' if srv else ('DOWN' if srv is False else 'n/a')}"
                 f" mem_avail={snap.get('mem_available_mib')}MiB cgroup={snap.get('cgroup_current_mib')}/{snap.get('cgroup_limit_mib') or 'unlimited'}MiB oom_kill={snap.get('oom_kill')}")
            timeline.append({"t": round(el), "running": st["running"], "last": ev_name, "server_up": srv, **snap})
            if not st["running"]:
                mk.mark("child_exit_observed", stage=stage, returncode=st["returncode"], signal=st["killed_by_signal"], last_event=ev_name)
                exit_marked = True
                break
            if el > dur + grace_s:
                break
    finally:
        mk.mark("before_supervisor_cleanup", stage=stage, child_running=p.running)
        rep = p.stop(grace_s=10, kill_after_s=5)                                      # only ever signals this child's own process group
        mk.mark("after_supervisor_cleanup", stage=stage, returncode=rep.get("returncode"), signals_sent=rep.get("signals_sent"),
                signals_refused=rep.get("signals_refused"), child_exit_seen_before_cleanup=exit_marked)
    summary = None
    for line in reversed(p.tail("stdout", 5)):
        try:
            cand = json.loads(line)
            if isinstance(cand, dict) and "stage" in cand:
                summary = cand
                break
        except ValueError:
            continue
    ev = _events(p.diag_path)
    checks = evaluate(stage, ev, summary, rep, dur, mode=mode)
    after = system_snapshot()
    oom_delta = None if base.get("oom_kill") is None or after.get("oom_kill") is None else after["oom_kill"] - base["oom_kill"]
    checks.append(("no OS out-of-memory kill during the stage", oom_delta in (None, 0), f"cgroup oom_kill delta={oom_delta}"))
    if server is not None:
        checks.append(("the vLLM server process stayed up", bool(getattr(server, "running", False)), f"running={getattr(server, 'running', None)} pid={getattr(server, 'pid', None)}"))
    passed = all(ok for _, ok, _ in checks)
    echo(f"\n[stage {stage}] {'PASS' if passed else 'FAIL'}")
    for name, ok, detail in checks:
        echo(f"  {'PASS' if ok else 'FAIL'}  {name}  [{redact(detail)}]")
    if not passed:
        echo("\n  last diagnostic events (the LAST line is the last thing the connector finished):")
        for line in p.tail("diag", 8):
            echo("   ", line[:240])
        for which in ("faulthandler", "stderr"):
            t = p.tail(which, 12)
            if t:
                echo(f"  --- {which} tail ---")
                for line in t:
                    echo("   ", line[:240])
    mk.mark("before_stage_return", stage=stage, passed=passed)
    return {"stage": stage, "passed": passed, "checks": checks, "exit": rep, "summary": summary, "timeline": timeline, "dir": str(out_dir), "pid": pid,
            "events": len(ev), "oom_kill_delta": oom_delta}
