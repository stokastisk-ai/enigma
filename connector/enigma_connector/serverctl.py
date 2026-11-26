"""External orchestration around the IMMUTABLE Stokastisk build-5 ``ServerProcess`` (the installed wheel is never patched).

Two known build-5 limitations (recorded as expectedFailure tests in the Stokastisk repository) are handled here, outside the package:

1. ``ServerProcess.stop()`` called AFTER the server's leader has exited and been reaped probes ``killpg(pgid, 0)`` and SIGKILLs whatever group now has
   that number; a reused PGID owned by an unrelated session (on Kaggle everything is root) could be killed. ``safe_stop`` therefore only calls the
   package's ``stop()`` while the leader is still unreaped (its PID, and so the group id, is still ours). Otherwise it never calls ``stop()``: it
   signals surviving members one by one through pidfds after re-verifying process group AND session, refuses any process that holds the PGID number,
   and neutralises the object so a later accidental ``stop()`` is a no-op.
2. Loopback HTTP in build 5 (``health()``, ``stream_chat``, ``fetch_metrics``) honours ``http_proxy`` unless ``no_proxy`` lists the loopback hosts.
   ``prepare_loopback_env`` adds them to ``no_proxy``/``NO_PROXY`` (the notebook process only) BEFORE the server is started or polled.
External HTTPS (the Enigma API) is unaffected: it keeps honouring proxy settings and verifying certificates."""
from __future__ import annotations

import logging
import os
import signal
from typing import Any

from .localnet import ensure_loopback_bypass

log = logging.getLogger("enigma.serverctl")


def prepare_loopback_env(environ: Any = None) -> dict[str, str]:
    """Call once, before ``ServerProcess.start()``. Returns the resulting no_proxy values (for the diagnostic record)."""
    env = os.environ if environ is None else environ
    ensure_loopback_bypass(env)
    return {"no_proxy": env.get("no_proxy", ""), "NO_PROXY": env.get("NO_PROXY", "")}


def _proc_ids(pid: int) -> tuple[int, int] | None:
    """(process group, session) of ``pid`` from /proc, or None if it is gone or a zombie."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            rest = fh.read().rsplit(b")", 1)[1].split()
    except OSError:
        return None
    if rest[0] in (b"Z", b"X"):
        return None
    return int(rest[2]), int(rest[3])


def group_members(pgid: int) -> list[int]:
    """Live processes whose process group AND session both equal ``pgid``, excluding the PGID number itself (after the leader is reaped a process
    holding that number cannot be one of ours: Linux never allocates a PID that is still in use as a group id)."""
    out: list[int] = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return out
    for n in names:
        if n.isdigit() and int(n) != pgid and _proc_ids(int(n)) == (pgid, pgid):
            out.append(int(n))
    return out


def kill_verified_orphans(pgid: int) -> dict[str, int]:
    """SIGKILL surviving members of OUR (already leaderless) group one at a time through pidfds, re-verifying identity after pinning. Never killpg."""
    if getattr(os, "pidfd_open", None) is None or getattr(signal, "pidfd_send_signal", None) is None:
        return {"signalled": 0, "refused": len(group_members(pgid)), "pidfd": 0}
    done = refused = 0
    for pid in group_members(pgid):
        try:
            fd = os.pidfd_open(pid)
        except OSError:
            continue
        try:
            if _proc_ids(pid) != (pgid, pgid):
                refused += 1
                continue
            signal.pidfd_send_signal(fd, signal.SIGKILL)
            done += 1
        except ProcessLookupError:
            log.debug("orphan %s exited before it was signalled", pid)
        except PermissionError:
            refused += 1
        finally:
            os.close(fd)
    return {"signalled": done, "refused": refused, "pidfd": 1}


def safe_stop(server: Any, grace: float = 30.0) -> dict[str, Any]:
    """Stop a build-5 ``ServerProcess`` without ever risking a signal to a process group that is not the server's. Returns a report (never raises
    for an already-stopped server)."""
    proc = getattr(server, "_proc", None)
    if proc is None:
        return {"action": "none", "reason": "not started or already stopped"}
    if proc.poll() is None:
        # Leader alive (or an unreaped zombie): its PID, and therefore its process-group id, is still ours; the package's own stop() is safe here.
        rc = server.stop(grace=grace)
        return {"action": "package_stop", "exit_code": rc, "report": dict(getattr(server, "shutdown_report", {}) or {})}
    pgid = getattr(server, "_pgid", None) or proc.pid
    res = kill_verified_orphans(pgid)
    log_fh = getattr(server, "_log", None)
    try:
        if log_fh:
            log_fh.close()
    except OSError:
        log.debug("server log handle already closed")
    # Neutralise the object: a later accidental server.stop() returns None instead of probing a possibly reused group id.
    server._proc, server._pgid, server._log = None, None, None
    return {"action": "leader_already_exited", "exit_code": proc.returncode, "orphans": res, "remaining": len(group_members(pgid))}
