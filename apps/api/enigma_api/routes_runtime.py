"""Runtime plane: called by the GPU connector with a scoped runtime token. Every handler returns promptly (no long-poll, no streaming)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from .deps import AppState, lease_owner, runtime_identity, state
from .errors import ApiError
from .models import (ClaimedJob, ClaimRequest, ClaimResponse, CompleteRequest, EventsRequest, EventsResponse, FailRequest, HeartbeatResponse, JobAck,
                     JobStatusRuntime, RegisterRequest, RegisterResponse, TelemetryBatch, TelemetryResponse)
from .security import RuntimeIdentity

router = APIRouter(prefix="/api/v1", tags=["runtime"])

_ERR = {  # store error code -> (http status, public code)
    "not_found": (404, "not_found"), "terminal": (409, "job_terminal"), "lease_lost": (409, "lease_lost"), "gap": (409, "sequence_gap"),
    "cap_exceeded": (413, "job_cap_exceeded"), "seq_mismatch": (409, "sequence_mismatch"), "hash_mismatch": (422, "hash_mismatch"),
    "conflict": (409, "conflict"), "not_cancel_requested": (409, "not_cancel_requested"), "batch_too_large": (413, "batch_too_large"),
    "run_not_found": (404, "run_not_found"),
}


def _raise(res: dict) -> None:
    status, code = _ERR.get(res["error"], (400, res["error"]))
    extra = {k: v for k, v in res.items() if k in ("expected_seq", "status", "last_event_seq")}
    raise ApiError(status, code, f"request rejected: {code}", extra=extra)


def _own(ident: RuntimeIdentity, runtime_id: str) -> None:
    if runtime_id != ident.runtime_id:
        raise ApiError(404, "not_found", "not found")        # a token can only address its own runtime


def _limit(st: AppState, name: str, key: str, rate: float, burst: int) -> None:
    from .ratelimit import RateLimiter
    st.limiters.setdefault(name, RateLimiter(rate, burst)).check(key)


@router.post("/runtimes/register", response_model=RegisterResponse)
def register(body: RegisterRequest, request: Request, ident: RuntimeIdentity = Depends(runtime_identity)):
    st = state(request)
    if body.is_mock != ident.is_mock:
        # a real-GPU credential must never carry mock data and vice versa: keeps example data distinguishable from measurements
        raise ApiError(409, "mock_mismatch", "this credential was issued for a " + ("mock" if ident.is_mock else "real") + " runtime")
    run_id = st.store.register_runtime(ident.runtime_id, {"served_model": body.served_model, "tp": body.tp, "pp": body.pp, "max_model_len": body.max_model_len,
                                                          "gpus": [g.model_dump() for g in body.gpus], "sdk": body.sdk.model_dump(), "capabilities": body.capabilities})
    st.store.audit(ident.workspace_id, f"runtime:{ident.runtime_id}", "runtime.register", "runtime", ident.runtime_id,
                   {"is_mock": ident.is_mock, "connector_version": body.connector_version})
    return RegisterResponse(runtime_id=ident.runtime_id, run_id=run_id)


@router.post("/runtimes/{runtime_id}/heartbeat", response_model=HeartbeatResponse)
def heartbeat(runtime_id: str, request: Request, ident: RuntimeIdentity = Depends(runtime_identity)):
    _own(ident, runtime_id)
    st = state(request)
    _limit(st, "heartbeat", ident.runtime_id, 2.0, 6)
    if st.store.heartbeat(ident.runtime_id) is None:
        raise ApiError(404, "not_found", "not found")
    return HeartbeatResponse(server_time=st.clock())


@router.post("/telemetry/batch", response_model=TelemetryResponse)
def telemetry(body: TelemetryBatch, request: Request, ident: RuntimeIdentity = Depends(runtime_identity)):
    st = state(request)
    _limit(st, "telemetry", ident.runtime_id, 2.0, 6)
    now = st.clock()
    for row in [*body.gpu, *body.vllm]:
        if row.captured_at > now + timedelta(seconds=60) or row.captured_at < now - timedelta(hours=24):
            raise ApiError(422, "timestamp_out_of_range", "captured_at must be within the last 24 h and not in the future")
    res = st.store.ingest_telemetry(ident.runtime_id, body.batch_id, body.run_id,
                                    [g.model_dump(mode="json") for g in body.gpu], [v.model_dump(mode="json") for v in body.vllm])
    if "error" in res:
        _raise(res)
    return TelemetryResponse(duplicate=bool(res.get("duplicate")), gpu_rows=res.get("gpu_rows", 0), vllm_rows=res.get("vllm_rows", 0))


@router.post("/jobs/claim", response_model=ClaimResponse)
def claim(request: Request, body: ClaimRequest | None = None, ident: RuntimeIdentity = Depends(runtime_identity), owner: str = Depends(lease_owner)):
    st = state(request)
    _limit(st, "claim", ident.runtime_id, 5.0, 10)
    body = body or ClaimRequest()
    job = st.store.claim_job(ident.runtime_id, owner, body.lease_seconds)
    if not job:
        return ClaimResponse(job=None, poll_after_ms=1000)
    st.broadcaster.send(job["conversation_id"], "status", {"job_id": job["id"], "status": "claimed", "attempt": job["attempt"]})
    return ClaimResponse(job=ClaimedJob(job_id=job["id"], conversation_id=job["conversation_id"], attempt=job["attempt"], lease_expires_at=job["lease_expires_at"],
                                        next_event_seq=job["last_event_seq"] + 1, request=job["request"]), poll_after_ms=0)


@router.post("/jobs/{job_id}/events", response_model=EventsResponse)
def events(job_id: str, body: EventsRequest, request: Request, ident: RuntimeIdentity = Depends(runtime_identity), owner: str = Depends(lease_owner)):
    st = state(request)
    _limit(st, "events", ident.runtime_id, 30.0, 60)
    res = st.store.append_events(ident.runtime_id, job_id, owner, [e.model_dump(mode="json") for e in body.events], body.lease_seconds)
    if "error" in res:
        _raise(res)
    if res["accepted"] > 0:
        job = st.store.get_job(job_id)
        first = res.get("first_new_seq") or 0
        fresh = [{"seq": e.event_seq, "kind": e.kind, "text": e.text} for e in body.events if e.event_seq >= first]
        if job:
            st.broadcaster.send(job["conversation_id"], "events", {"job_id": job_id, "events": fresh, "last_event_seq": res["last_event_seq"]})
    return EventsResponse(last_event_seq=res["last_event_seq"], accepted=res["accepted"], duplicates=res["duplicates"], cancel_requested=bool(res["cancel_requested"]))


def _finish_broadcast(st: AppState, job_id: str, extra: dict | None = None) -> None:
    job = st.store.get_job(job_id)
    if job:
        st.broadcaster.send(job["conversation_id"], "status", {"job_id": job_id, "status": job["status"], **(extra or {})})


@router.post("/jobs/{job_id}/complete", response_model=JobAck)
def complete(job_id: str, body: CompleteRequest, request: Request, ident: RuntimeIdentity = Depends(runtime_identity), owner: str = Depends(lease_owner)):
    st = state(request)
    res = st.store.complete_job(ident.runtime_id, job_id, owner, body.final_seq, body.text_sha256, body.result.model_dump(mode="json", exclude_none=True))
    if "error" in res:
        _raise(res)
    _finish_broadcast(st, job_id, {"assistant_message_id": res.get("assistant_message_id")})
    return JobAck(status="succeeded", idempotent=bool(res.get("idempotent")), assistant_message_id=res.get("assistant_message_id"))


@router.post("/jobs/{job_id}/fail", response_model=JobAck)
def fail(job_id: str, body: FailRequest, request: Request, ident: RuntimeIdentity = Depends(runtime_identity), owner: str = Depends(lease_owner)):
    st = state(request)
    res = st.store.fail_job(ident.runtime_id, job_id, owner, body.code, body.message, body.retryable)
    if "error" in res:
        _raise(res)
    _finish_broadcast(st, job_id)
    return JobAck(status=res.get("status"), idempotent=bool(res.get("idempotent")))


@router.post("/jobs/{job_id}/ack-cancel", response_model=JobAck)
def ack_cancel(job_id: str, request: Request, ident: RuntimeIdentity = Depends(runtime_identity), owner: str = Depends(lease_owner)):
    st = state(request)
    res = st.store.ack_cancel(ident.runtime_id, job_id, owner)
    if "error" in res:
        _raise(res)
    _finish_broadcast(st, job_id)
    return JobAck(status=res.get("status"), idempotent=bool(res.get("idempotent")))


@router.get("/jobs/{job_id}/status", response_model=JobStatusRuntime)
def job_status(job_id: str, request: Request, ident: RuntimeIdentity = Depends(runtime_identity)):
    job = state(request).store.get_job(job_id)
    if not job or job["runtime_id"] != ident.runtime_id:
        raise ApiError(404, "not_found", "not found")
    return JobStatusRuntime(job_id=job_id, status=job["status"], cancel_requested=job["status"] == "cancel_requested", last_event_seq=job["last_event_seq"])
