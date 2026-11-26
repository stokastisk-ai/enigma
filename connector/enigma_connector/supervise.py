"""Run the connector as a SEPARATE process and watch it from the notebook.

Why: a connector thread that dies inside the notebook kernel can take the whole kernel (and its logs) with it. As a child process its stdout, stderr, diagnostic
log and faulthandler dump land in files, and its exit code or terminating signal is recorded by the parent instead of being lost.

Safety:
* The runtime key is passed through the child's ENVIRONMENT only, never on the command line (visible in process listings) and never printed.
* The child is started in its own session/process group. ``stop()`` signals that group only, after checking it really is the child's own group,
  so it can never signal the notebook kernel or the vLLM server.
* Shutdown is bounded: SIGTERM, a grace period, then SIGKILL, then a final bounded wait."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from .diag import redact


class ConnectorProcess:
    def __init__(self, args: Sequence[str], env: dict[str, str], workdir: str | os.PathLike[str], *, command: Sequence[str] | None = None,
                 popen: Callable[..., Any] = subprocess.Popen, clock: Callable[[], float] = time.monotonic):
        self._args = list(args)
        self._env = dict(env)                                   # held privately; never exposed by __repr__ / status()
        self.dir = Path(workdir)
        self._command = list(command) if command is not None else [sys.executable, "-X", "faulthandler", "-m", "enigma_connector"]
        self._popen, self._clock = popen, clock
        self.proc: Any = None
        self._t0: float | None = None
        self.stdout_path, self.stderr_path = self.dir / "connector.stdout.log", self.dir / "connector.stderr.log"
        self.diag_path, self.fault_path = self.dir / "connector.diag.jsonl", self.dir / "connector.faulthandler.log"
        self._fhs: list[Any] = []
        self.sent_signals: list[str] = []                       # evidence: every signal this supervisor actually delivered
        self.refused_signals: list[dict[str, Any]] = []         # evidence: every signal it refused to deliver, and why
        self._stop_lock = threading.Lock()                      # stop() is strictly serial: Popen.poll() returns None while ANOTHER thread is reaping the child

    def __repr__(self) -> str:                                  # no env, no args: nothing secret can leak through a print()
        return f"<ConnectorProcess pid={getattr(self.proc, 'pid', None)} running={self.running}>"

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> int:
        if self.proc is not None:
            raise RuntimeError("this ConnectorProcess was already started")
        self.dir.mkdir(parents=True, exist_ok=True)
        out, err = open(self.stdout_path, "ab"), open(self.stderr_path, "ab")           # noqa: SIM115 - closed in _close_files()
        self._fhs += [out, err]
        extra = ["--diag-log", str(self.diag_path), "--faulthandler-log", str(self.fault_path)] if self._command[-1] == "enigma_connector" else []
        try:
            self.proc = self._popen([*self._command, *extra, *self._args], env=self._env, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                    start_new_session=True, close_fds=True, cwd=str(self.dir))
        except BaseException:
            self._close_files()                                 # a failed spawn must not leak the two log handles
            raise
        finally:
            self._env = {}                                      # the key is not kept in this (notebook) process any longer than needed, even on failure
        self._t0 = self._clock()
        return self.proc.pid

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def poll(self) -> int | None:
        return None if self.proc is None else self.proc.poll()

    def wait(self, timeout: float) -> int | None:
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def stop(self, grace_s: float = 10.0, kill_after_s: float = 5.0) -> dict[str, Any]:
        """SIGTERM -> wait ``grace_s`` -> SIGKILL -> wait ``kill_after_s``. Only this child's own process group is signalled. Safe to call repeatedly and concurrently."""
        with self._stop_lock:
            escalated = False
            if self.running:
                self._signal(signal.SIGTERM)
                if self.wait(grace_s) is None:
                    escalated = True
                    self._signal(signal.SIGKILL)
                    self.wait(kill_after_s)
            rep = self.status()
            rep["escalated_to_sigkill"] = escalated
            rep["signals_sent"] = list(self.sent_signals)
            rep["signals_refused"] = list(self.refused_signals)
            self._close_files()
            return rep

    def _signal(self, sig: int) -> bool:
        """Signal the child's own process group, or nothing. Returns True only if a signal was actually sent.

        Identity rules (each is required):
        * ``poll()`` must still report the child as not reaped. Until WE reap it, the kernel keeps its PID (and therefore its group id) reserved,
          even as a zombie, so the PID cannot have been reused by an unrelated process.
        * the PID must still lead its own process group AND its own session (``start_new_session=True``). A session leader cannot leave its group,
          so if this is false the PID is no longer our child: nothing is signalled. (Earlier revisions fell back to ``os.kill(pid)`` here, which
          could only ever reach a DIFFERENT process.)
        * the group must never be the notebook's own group or session."""
        pid = self.proc.pid
        if self.proc.poll() is not None:                         # already exited and reaped: there is nothing of ours left to signal
            return False
        try:
            if os.getpgid(pid) != pid or os.getsid(pid) != pid or pid in (os.getpgid(0), os.getsid(0)):
                self.refused_signals.append({"signal": signal.Signals(sig).name, "pid": pid, "reason": "pid_is_not_our_session_leader"})
                return False
            os.killpg(pid, sig)
        except ProcessLookupError:
            return False
        self.sent_signals.append(signal.Signals(sig).name)
        return True

    def _close_files(self) -> None:
        for fh in self._fhs:
            try:
                fh.close()
            except OSError:
                pass
        self._fhs = []

    # ------------------------------------------------------------------ observation
    def status(self) -> dict[str, Any]:
        rc = self.poll()
        sig = None
        if rc is not None and rc < 0:
            try:
                sig = signal.Signals(-rc).name
            except ValueError:
                sig = f"SIG{-rc}"
        size = lambda p: p.stat().st_size if p.exists() else 0      # noqa: E731
        return {"pid": getattr(self.proc, "pid", None), "running": rc is None and self.proc is not None, "returncode": rc, "killed_by_signal": sig,
                "uptime_s": None if self._t0 is None else round(self._clock() - self._t0, 1),
                "bytes": {"stdout": size(self.stdout_path), "stderr": size(self.stderr_path), "diag": size(self.diag_path), "faulthandler": size(self.fault_path)}}

    def tail(self, which: str = "diag", lines: int = 20) -> list[str]:
        """Last ``lines`` lines of a log, redacted again on the way out."""
        path = {"stdout": self.stdout_path, "stderr": self.stderr_path, "diag": self.diag_path, "faulthandler": self.fault_path}[which]
        if not path.exists():
            return []
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 65536))
            text = fh.read().decode("utf-8", "replace")
        return [redact(line) for line in text.splitlines()[-lines:]]

    def last_event(self) -> dict | None:
        """The last complete diagnostic event written: after a hard crash, this is the last thing the child managed to do."""
        import json
        for line in reversed(self.tail("diag", 5)):
            try:
                return json.loads(line)
            except ValueError:
                continue
        return None
