"""Connector main loop: register, heartbeat, telemetry (with spool), job polling. All traffic is OUTBOUND HTTPS; nothing listens.

Threads: heartbeat, telemetry sampler/uploader, job worker. A fatal AuthError (token revoked/rotated/expired) stops everything.
Offline behaviour: heartbeats/claims retry with jittered backoff; telemetry batches go to the bounded spool and are replayed in order."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from . import __version__
from .config import Config
from .http import ApiClient, ApiError, AuthError, Transient, backoff_delays
from .jobs import JobExecutor, StreamingLlm
from .pacing import ClaimPacer
from .spool import Spool

log = logging.getLogger("enigma.runner")


class Connector:
    def __init__(self, cfg: Config, api: ApiClient, llm: StreamingLlm, gpu: Any, vllm: Any, spool: Spool, *, is_mock: bool = False,
                 sdk_identity: dict | None = None, on_job_done: Callable[[str], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.cfg, self.api, self.llm, self.gpu, self.vllm, self.spool = cfg, api, llm, gpu, vllm, spool
        self.is_mock, self.sdk_identity, self.sleep = is_mock, sdk_identity or {}, sleep
        self.on_job_done = on_job_done
        self.stop_event = threading.Event()
        self.fatal: Exception | None = None
        self.run_id: str | None = None
        self.runtime_id: str | None = None
        self.stats = {"batches_sent": 0, "batches_spooled": 0, "batches_dropped": 0, "jobs_done": 0, "jobs_failed": 0, "heartbeats_failed": 0}
        self._threads: list[threading.Thread] = []
        self.executor = JobExecutor(api, llm, cfg, sleep=sleep, is_mock=is_mock)
        self._gpu_buf: list[dict] = []
        self._vllm_buf: list[dict] = []
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- lifecycle
    def _wait(self, seconds: float) -> bool:
        """Interruptible sleep. True if we should stop."""
        return self.stop_event.wait(seconds)

    def register(self) -> dict:
        inv = self.gpu.inventory() if self.gpu else []
        caps = {"nvml": self.gpu is not None, "vllm_metrics": self.vllm is not None, "mock": self.is_mock}
        body = {"connector_version": __version__, "is_mock": self.is_mock, "served_model": self.cfg.served_model or "unknown", "tp": self.cfg.tp, "pp": self.cfg.pp,
                "max_model_len": self.cfg.max_model_len,
                "gpus": [{k: g.get(k) for k in ("index", "uuid", "name", "memory_total_mib")} for g in inv],
                "sdk": {k: v for k, v in {**self.sdk_identity, "driver_version": (inv[0].get("driver") if inv else None)}.items() if v}, "capabilities": caps}
        for delay in backoff_delays(1.0, 30.0):
            try:
                r = self.api.post("/api/v1/runtimes/register", body)
                self.run_id, self.runtime_id = r["run_id"], r["runtime_id"]
                return r
            except Transient:
                log.warning("register: API unreachable, retrying")
                if self._wait(delay):
                    raise RuntimeError("stopped before registration") from None

    def start(self) -> None:
        self.register()
        for name, fn in (("heartbeat", self._heartbeat_loop), ("telemetry", self._telemetry_loop), ("jobs", self._job_loop)):
            t = threading.Thread(target=self._guard(fn), name=f"enigma-{name}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 10.0) -> None:
        self.stop_event.set()
        for t in self._threads:
            t.join(timeout)
        try:
            self._upload_telemetry(final=True)
        except Exception:                                        # noqa: BLE001 - shutting down; the spool keeps what failed
            log.debug("final telemetry flush failed", exc_info=True)

    def _guard(self, fn):
        def run():
            try:
                fn()
            except AuthError as e:
                self.fatal = e
                log.error("credential rejected (%s): stopping connector; issue a new token in Enigma", e.code)
                self.stop_event.set()
            except Exception as e:                               # noqa: BLE001
                self.fatal = e
                log.error("connector thread crashed: %s", type(e).__name__)
                self.stop_event.set()
        return run

    # ---------------------------------------------------------------- heartbeat
    def _heartbeat_loop(self) -> None:
        delays = backoff_delays(1.0, 15.0)
        while not self.stop_event.is_set():
            try:
                self.api.post(f"/api/v1/runtimes/{self.runtime_id}/heartbeat")
                delays = backoff_delays(1.0, 15.0)
                if self._wait(self.cfg.heartbeat_interval_s):
                    return
            except Transient:
                self.stats["heartbeats_failed"] += 1
                if self._wait(next(delays)):
                    return

    # ---------------------------------------------------------------- telemetry
    def _take_batch(self) -> dict | None:
        with self._lock:
            if not self._gpu_buf and not self._vllm_buf:
                return None
            gpu, self._gpu_buf = self._gpu_buf[:self.cfg.max_batch_gpu_rows], self._gpu_buf[self.cfg.max_batch_gpu_rows:]
            vl, self._vllm_buf = self._vllm_buf[:self.cfg.max_batch_vllm_rows], self._vllm_buf[self.cfg.max_batch_vllm_rows:]
        return {"batch_id": uuid.uuid4().hex, "run_id": self.run_id, "gpu": gpu, "vllm": vl}

    def _post_batch(self, batch: dict) -> bool:
        """True = delivered or permanently rejected (drop). False = keep for retry. AuthError propagates."""
        try:
            self.api.post("/api/v1/telemetry/batch", batch)
            self.stats["batches_sent"] += 1
            return True
        except Transient:
            return False
        except ApiError as e:
            if e.code in ("run_not_found",):
                batch["run_id"] = self.run_id                                    # a re-registration changed the run: retarget once
                return False
            self.stats["batches_dropped"] += 1                                   # validation failure: poison batch, never block the queue
            log.warning("telemetry batch rejected (%s): dropped", e.code)
            return True

    def _upload_telemetry(self, final: bool = False) -> None:
        while True:                                                              # replay older spooled batches first, oldest-first
            item = self.spool.peek()
            if not item:
                break
            path, batch = item
            batch["run_id"] = batch.get("run_id") if batch.get("run_id") == self.run_id else None   # a stale run id from before a restart is dropped
            if not self._post_batch(batch):
                # still offline: move freshly collected samples to the spool too (in order, after the older ones) so the in-memory
                # buffer stays small and a long outage is bounded by the spool's byte cap, not by RAM
                while (fresh := self._take_batch()):
                    self.spool.put(fresh)
                    self.stats["batches_spooled"] += 1
                return
            self.spool.ack(path)
            if not final and self.stop_event.is_set():
                return
        batch = self._take_batch()
        while batch:
            if not self._post_batch(batch):
                self.spool.put(batch)
                self.stats["batches_spooled"] += 1
                break
            batch = self._take_batch()

    def _telemetry_loop(self) -> None:
        last_upload = time.monotonic()
        last_vllm = 0.0
        while not self.stop_event.is_set():
            t0 = time.monotonic()
            try:
                if self.gpu is not None:
                    with self._lock:
                        self._gpu_buf.extend(self.gpu.sample())
                        del self._gpu_buf[:-self.cfg.max_batch_gpu_rows * 4]
                if self.vllm is not None and t0 - last_vllm >= self.cfg.vllm_interval_s:
                    last_vllm = t0
                    s = self.vllm.sample()
                    if s:
                        with self._lock:
                            self._vllm_buf.append(s)
                            del self._vllm_buf[:-self.cfg.max_batch_vllm_rows * 4]
                if t0 - last_upload >= self.cfg.upload_interval_s:
                    last_upload = t0
                    self._upload_telemetry()
            except AuthError:
                raise
            except Exception as e:                                               # noqa: BLE001 - sampling must never kill the connector
                log.warning("telemetry tick failed: %s", type(e).__name__)
            self._wait(max(0.0, self.cfg.gpu_interval_s - (time.monotonic() - t0)))

    # ---------------------------------------------------------------- jobs
    def _job_loop(self) -> None:
        delays = backoff_delays(0.5, 15.0)
        pacer = ClaimPacer(self.cfg.claim_poll_s, self.cfg.claim_poll_idle_s, self.cfg.claim_hot_window_s)
        while not self.stop_event.is_set():
            try:
                r = self.api.post("/api/v1/jobs/claim", {"lease_seconds": self.cfg.lease_seconds})
                delays = backoff_delays(0.5, 15.0)
            except Transient:
                if self._wait(next(delays)):
                    return
                continue
            except ApiError as e:
                log.warning("claim rejected: %s", e.code)
                if self._wait(2.0):
                    return
                continue
            job = r.get("job")
            if not job:
                if self._wait(pacer.next_wait(r.get("poll_after_ms", 1000) / 1000.0 if pacer.idle == pacer.fast else 0.0)):
                    return
                continue
            try:
                outcome = self.executor.run(job)
            except AuthError:
                raise
            except Exception as e:                                               # noqa: BLE001
                log.error("job %s crashed: %s", job["job_id"][:8], type(e).__name__)
                outcome = "failed"
            pacer.on_activity()
            self.stats["jobs_done" if outcome in ("succeeded", "cancelled") else "jobs_failed"] += 1
            if self.on_job_done:
                self.on_job_done(outcome)
