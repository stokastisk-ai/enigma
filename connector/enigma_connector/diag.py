"""Crash-evidence tooling for the connector (opt-in; nothing here runs unless a diagnostic option is given).

A notebook kernel that dies hard (segfault in a native library, OOM kill, supervisor kill) leaves no Python traceback and no output. To make such an event
diagnosable the connector can run as its own process and write an append-only JSON-lines log that is flushed and fsynced on every line, so the LAST line
on disk is the last thing the process managed to do. ``faulthandler`` is pointed at its own file so a fatal signal (SIGSEGV, SIGABRT, SIGBUS, SIGFPE, SIGILL)
leaves the Python stack of every thread behind.

Everything written is passed through :func:`redact` first: runtime keys, bearer tokens, JWTs and database URLs never reach a log."""
from __future__ import annotations

import faulthandler
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"enig_rt_[a-z0-9]{8}_[A-Za-z0-9_-]{6,}"), "enig_rt_<redacted>"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer <redacted>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"), "<jwt-redacted>"),
    (re.compile(r"postgres(?:ql)?://\S+", re.IGNORECASE), "<dsn-redacted>"),
    (re.compile(r"(?i)\b(?:password|passwd|secret|token)\s*[=:]\s*\S+"), "<secret-redacted>"),
)


def redact(value: Any) -> Any:
    """Recursively redact secrets in strings inside dicts/lists/tuples. Non-string scalars pass through."""
    if isinstance(value, str):
        for pat, repl in _REDACTIONS:
            value = pat.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {str(redact(k)): redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class DiagLog:
    """Append-only JSONL event log. Every line is flushed and fsynced; write failures never raise into the connector."""

    def __init__(self, path: str | os.PathLike[str] | None, *, clock: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic, fsync: bool = True):
        self.path = None if path is None else str(path)
        self._clock, self._mono, self._fsync = clock, mono, fsync
        self._t0 = mono()
        self._lock = threading.Lock()
        self._fh = None
        self.seq = 0
        if self.path is not None:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            self._fh = open(self.path, "a", buffering=1, encoding="utf-8")        # noqa: SIM115  (kept open for the life of the process)

    def emit(self, event: str, **fields: Any) -> None:
        with self._lock:
            self.seq += 1
            rec = {"seq": self.seq, "t": datetime.fromtimestamp(self._clock(), timezone.utc).isoformat(timespec="milliseconds"),
                   "up_s": round(self._mono() - self._t0, 3), "pid": os.getpid(), "thread": threading.current_thread().name, "event": event, **fields}
            try:
                line = json.dumps(redact(rec), separators=(",", ":"), default=str)
            except (TypeError, ValueError):
                line = json.dumps({"seq": self.seq, "event": "diag_serialisation_error", "of": event})
            if self._fh is None:
                return
            try:
                self._fh.write(line + "\n")
                self._fh.flush()
                if self._fsync:
                    os.fsync(self._fh.fileno())
            except (OSError, ValueError):
                pass                                                               # a full disk must never take the connector down

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                finally:
                    self._fh = None


NULL_DIAG = DiagLog(None)


class FaultHandler:
    """``faulthandler`` writing to its own file. ``hang_dump_s`` > 0 additionally dumps every thread's stack that often (finds a hang, not just a crash)."""

    def __init__(self, path: str | os.PathLike[str], hang_dump_s: float = 0.0):
        os.makedirs(os.path.dirname(os.path.abspath(str(path))), exist_ok=True)
        self._fh = open(str(path), "a", buffering=1, encoding="utf-8")            # noqa: SIM115
        self.hang_dump_s = float(hang_dump_s)
        faulthandler.enable(file=self._fh, all_threads=True)
        if self.hang_dump_s > 0:
            faulthandler.dump_traceback_later(self.hang_dump_s, repeat=True, file=self._fh)

    def close(self) -> None:
        try:
            if self.hang_dump_s > 0:
                faulthandler.cancel_dump_traceback_later()
            faulthandler.disable()
        finally:
            self._fh.close()
