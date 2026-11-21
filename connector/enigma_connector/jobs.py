"""Executes one claimed job: stream tokens from the local model server and relay them, batched and ordered, to the Enigma API.

Protocol guarantees (see docs/ARCHITECTURE.md):
  * event_seq is monotonic per job and continues across attempts; a retried attempt starts with a ``reset`` event.
  * events stay in memory until the API acknowledges them (``last_event_seq``); a gap or a transient failure resends from there.
  * completion carries the SHA-256 of the full text and the final sequence; the server recomputes both before storing the message.
  * prompts and generated text are never logged here.
"""
from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
from typing import Any, Callable, Iterator, Protocol

from .config import Config
from .http import ApiClient, ApiError, AuthError, Transient

log = logging.getLogger("enigma.jobs")
MAX_EVENTS_PER_REQUEST = 64
MAX_BATCH_CHUNKS = 32


class StreamingLlm(Protocol):
    def stream(self, messages: list[dict], *, max_tokens: int, temperature: float, model: str | None) -> Iterator[str]: ...


class StokastiskLlm:
    """Uses the SDK's public ``stokastisk.serving.stream_chat`` against the LOCAL server started by ``ServerProcess``."""

    def __init__(self, base_url: str, served_model: str, read_timeout: float = 60.0):
        try:
            from stokastisk import serving
        except ImportError as e:
            raise RuntimeError("stokastisk is not installed in this environment; run this mode on the GPU host after installing the pinned wheel") from e
        self._serving, self.base_url, self.model, self.timeout = serving, base_url, served_model, read_timeout

    def stream(self, messages, *, max_tokens, temperature, model):
        return self._serving.stream_chat(self.base_url, model or self.model, messages, max_tokens=max_tokens, temperature=temperature, timeout=self.timeout)


def classify_error(e: BaseException) -> tuple[str, str, bool]:
    """(code, public message, retryable). Never includes the upstream body, which could echo prompt text."""
    status = getattr(e, "status", None)
    msg = str(e.args[0]) if e.args else type(e).__name__
    if isinstance(status, int):
        if 400 <= status < 500:
            return f"upstream_{status}", f"model server rejected the request (HTTP {status})", False
        return f"upstream_{status}", f"model server error (HTTP {status})", True
    if "could not reach" in msg:
        return "upstream_unreachable", "model server unreachable", True
    if "[DONE]" in msg or "truncated" in msg:
        return "stream_truncated", "model stream ended unexpectedly", True
    return "connector_error", f"{type(e).__name__} while generating", False


class _Aborted(Exception):
    """The lease is gone (another worker owns the job) or the job is terminal: stop quietly."""


class JobExecutor:
    def __init__(self, api: ApiClient, llm: StreamingLlm, cfg: Config, *, mono: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, is_mock: bool = False):
        self.api, self.llm, self.cfg, self.mono, self.sleep, self.is_mock = api, llm, cfg, mono, sleep, is_mock

    # ---------------------------------------------------------------- upload with ack tracking
    def _send(self, job_id: str, unacked: list[dict]) -> dict:
        """Send everything unacked (<=64 per request). Returns {'cancel': bool}. Mutates `unacked` to the still-unacknowledged tail."""
        cancel = False
        gap_retries = 0
        while unacked:
            chunk = unacked[:MAX_EVENTS_PER_REQUEST]
            try:
                r = self.api.post(f"/api/v1/jobs/{job_id}/events", {"events": chunk, "lease_seconds": self.cfg.lease_seconds})
            except ApiError as e:
                if e.code == "sequence_gap" and gap_retries < 3:
                    gap_retries += 1
                    expected = e.extra.get("expected_seq")
                    unacked[:] = [ev for ev in unacked if ev["event_seq"] >= expected]      # drop what the server already has, resend the rest
                    continue
                if e.code in ("lease_lost", "job_terminal", "not_found"):
                    raise _Aborted(e.code) from None
                raise
            last = r["last_event_seq"]
            unacked[:] = [ev for ev in unacked if ev["event_seq"] > last]
            cancel = cancel or bool(r.get("cancel_requested"))
        return {"cancel": cancel}

    # ---------------------------------------------------------------- main
    def run(self, job: dict) -> str:
        job_id, attempt, seq = job["job_id"], job["attempt"], job["next_event_seq"]
        req = job["request"]
        unacked: list[dict] = []
        parts: list[str] = []                 # text of THIS attempt only
        if attempt > 1:
            unacked.append({"event_seq": seq, "kind": "reset"})
            seq += 1

        q: queue.Queue = queue.Queue()
        stop = threading.Event()

        def produce():
            try:
                gen = self.llm.stream(req["messages"], max_tokens=req["max_tokens"], temperature=req["temperature"], model=req.get("model"))
                try:
                    for delta in gen:
                        if stop.is_set():
                            break
                        q.put(("delta", delta, self.mono()))
                finally:
                    close = getattr(gen, "close", None)
                    if close:
                        close()                  # closing the SSE response lets vLLM abort the request
                q.put(("done", None, self.mono()))
            except BaseException as e:           # noqa: BLE001 - reported to the API, never raised into the thread
                q.put(("error", e, self.mono()))

        t_start = self.mono()
        th = threading.Thread(target=produce, name=f"job-{job_id[:8]}", daemon=True)
        th.start()
        pending: list[str] = []
        chunks = 0
        t_first: float | None = None
        last_flush = last_send = t_start
        first_flushed = False
        cancelled = False
        failure: BaseException | None = None
        offline_since: float | None = None
        done = False

        def flush(force_keepalive: bool = False) -> None:
            nonlocal seq, pending, last_flush, last_send, first_flushed, cancelled, offline_since
            if pending:
                unacked.append({"event_seq": seq, "kind": "delta", "text": "".join(pending), "token_count": len(pending)})
                seq += 1
                pending = []
                first_flushed = True
                last_flush = self.mono()
            elif force_keepalive and not unacked:
                unacked.append({"event_seq": seq, "kind": "status"})
                seq += 1
            if not unacked:
                return
            try:
                res = self._send(job_id, unacked)
                offline_since = None
                last_send = self.mono()
                if res["cancel"] and not cancelled:
                    cancelled = True
                    stop.set()
            except Transient:
                offline_since = offline_since or self.mono()
                if self.mono() - offline_since > 2 * self.cfg.lease_seconds:
                    raise _Aborted("offline_too_long") from None
                self.sleep(0.2)

        try:
            while not done:
                try:
                    kind, val, t = q.get(timeout=self.cfg.flush_interval_s)
                except queue.Empty:
                    kind, val, t = None, None, self.mono()
                if kind == "delta":
                    if t_first is None:
                        t_first = t
                    parts.append(val)
                    pending.append(val)
                    chunks += 1
                elif kind == "error":
                    failure = val
                    done = True
                elif kind == "done":
                    done = True
                now = self.mono()
                if pending and (not first_flushed or now - last_flush >= self.cfg.flush_interval_s or len(pending) >= MAX_BATCH_CHUNKS or done):
                    flush()                                                 # the first chunk goes out immediately: that is the browser's TTFT
                elif not pending and unacked:
                    flush()                                                 # retry a transient failure
                elif not pending and now - last_send >= self.cfg.keepalive_s and not done:
                    flush(force_keepalive=True)                             # renew the lease during a long prefill; also surfaces cancel
                if cancelled:
                    break
            t_end = self.mono()
            stop.set()
            th.join(timeout=5)
            flush()                                                         # persist whatever is left (also the partial text of a cancelled job)
            for _ in range(5):
                if not unacked:
                    break
                self.sleep(0.5)
                flush()
            if unacked:
                raise _Aborted("could_not_deliver_events")
            if cancelled:
                self.api.post(f"/api/v1/jobs/{job_id}/ack-cancel")
                return "cancelled"
            if failure is not None:
                code, message, retryable = classify_error(failure)
                self.api.post(f"/api/v1/jobs/{job_id}/fail", {"code": code, "message": message, "retryable": retryable})
                log.warning("job %s failed: %s", job_id[:8], code)
                return "failed"
            text = "".join(parts)
            e2e = (t_end - t_start) * 1000.0
            result: dict[str, Any] = {"ttft_ms": (t_first - t_start) * 1000.0 if t_first is not None else None, "e2e_ms": e2e, "stream_chunks": chunks,
                                      "model": req.get("model"), "tp": self.cfg.tp, "pp": self.cfg.pp, "max_model_len": self.cfg.max_model_len,
                                      "is_mock": self.is_mock}
            if t_first is not None and chunks > 1 and t_end > t_first:
                result["chunks_per_s"] = (chunks - 1) / (t_end - t_first)
            result = {k: v for k, v in result.items() if v is not None}
            self.api.post(f"/api/v1/jobs/{job_id}/complete", {"final_seq": seq - 1, "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "result": result})
            return "succeeded"
        except _Aborted as a:
            stop.set()
            log.warning("job %s abandoned: %s", job_id[:8], a)
            return "abandoned"
        except AuthError:
            stop.set()
            raise
