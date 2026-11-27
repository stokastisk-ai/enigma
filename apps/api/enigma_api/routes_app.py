"""User plane: Neon-authenticated (via the Next.js BFF). Membership is checked on EVERY route; non-members get 404."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request

from . import series
from .deps import AppState, aware, require_member, runtime_status, state, user_identity, is_admin
from .errors import ApiError
from .models import (ConversationCreate, JobView, ProjectCreate, RuntimeCreate, RuntimeTokenResponse, SubmitJob, SubmitResponse, WorkspaceCreate)
from .ratelimit import RateLimiter
from .security import UserIdentity, generate_runtime_token

router = APIRouter(prefix="/api/v1/app", tags=["app"])
User = Annotated[UserIdentity, Depends(user_identity)]
Uuid = Annotated[str, Query(pattern=r"^[0-9a-fA-F-]{36}$")]
RANGES = {"5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
MAX_ROWS = 20000

_SUBMIT_ERR = {"not_found": (404, "not_found"), "runtime_not_found": (404, "runtime_not_found"), "prompt_too_long": (422, "prompt_too_long"),
               "too_many_jobs": (429, "too_many_jobs"), "too_many_user_jobs": (429, "too_many_user_jobs"), "idempotency_conflict": (409, "idempotency_conflict")}


def _uuid(v: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", v or ""):
        raise ApiError(404, "not_found", "not found")          # malformed ids look like missing ones
    return v.lower()


def _project(st: AppState, user: UserIdentity, project_id: str, min_role: str = "viewer") -> dict:
    p = st.store.get_project(_uuid(project_id))
    if not p:
        raise ApiError(404, "not_found", "not found")
    require_member(st, user, p["workspace_id"], min_role)
    return p


def _runtime(st: AppState, user: UserIdentity, runtime_id: str, min_role: str = "viewer") -> dict:
    rt = st.store.get_runtime(_uuid(runtime_id))
    if not rt:
        raise ApiError(404, "not_found", "not found")
    require_member(st, user, rt["workspace_id"], min_role)
    return rt


def _conversation(st: AppState, user: UserIdentity, cid: str, min_role: str = "viewer") -> dict:
    c = st.store.get_conversation(_uuid(cid))
    if not c:
        raise ApiError(404, "not_found", "not found")
    require_member(st, user, c["workspace_id"], min_role)
    return c


def _rt_view(rt: dict, now: datetime | None = None) -> dict:
    return {"id": rt["id"], "name": rt["name"], "project_id": rt["project_id"], "is_mock": rt["is_mock"], "served_model": rt.get("served_model"),
            "tp": rt.get("tp"), "pp": rt.get("pp"), "max_model_len": rt.get("max_model_len"), "capabilities": rt.get("capabilities") or {},
            "binary_identity_reported": rt.get("binary_identity") or {}, "last_heartbeat_at": rt.get("last_heartbeat_at"), **runtime_status(rt, now)}


def _issue(st: AppState, rt: dict, user: UserIdentity, days: int, rotated_from: str | None = None) -> RuntimeTokenResponse:
    token, prefix, digest = generate_runtime_token()
    exp = st.clock() + timedelta(days=days)
    cred = st.store.add_credential(rt, prefix, digest, exp, user.user_id, rotated_from)
    return RuntimeTokenResponse(runtime_id=rt["id"], credential_id=cred["id"], token=token, prefix=prefix, expires_at=exp)


@router.get("/me")
def me(request: Request, user: User):
    return {"user_id": user.user_id, "auth": user.auth, "is_admin": is_admin(request, user)}


# ------------------------------------------------------------------ tenancy
@router.get("/workspaces")
def list_workspaces(request: Request, user: User):
    return {"items": state(request).store.list_workspaces(user.user_id)}


@router.post("/workspaces", status_code=201)
def create_workspace(body: WorkspaceCreate, request: Request, user: User):
    st = state(request)
    st.limiters.setdefault("ws_create", RateLimiter(0.2, 3)).check(user.user_id)
    slug = body.slug or (re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")[:30] or "ws") + "-" + secrets.token_hex(2)
    if len(slug) < 2 or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,38}", slug):
        raise ApiError(422, "invalid_slug", "slug must be 2-39 chars of a-z, 0-9, '-'")
    res = st.store.create_workspace(user.user_id, body.name, slug)
    if "error" in res:
        raise ApiError(409 if res["error"] == "slug_taken" else 429, res["error"], "workspace could not be created")
    return res


@router.get("/workspaces/{workspace_id}/projects")
def list_projects(workspace_id: str, request: Request, user: User):
    st = state(request)
    require_member(st, user, _uuid(workspace_id))
    return {"items": st.store.list_projects(workspace_id)}


@router.post("/workspaces/{workspace_id}/projects", status_code=201)
def create_project(workspace_id: str, body: ProjectCreate, request: Request, user: User):
    st = state(request)
    require_member(st, user, _uuid(workspace_id), "admin")
    return st.store.create_project(workspace_id, body.name, user.user_id)


# ------------------------------------------------------------------ runtimes + credentials
@router.get("/projects/{project_id}/runtimes")
def list_runtimes(project_id: str, request: Request, user: User):
    st = state(request)
    _project(st, user, project_id)
    return {"items": [_rt_view(r, st.clock()) for r in st.store.list_runtimes(project_id)]}


@router.post("/projects/{project_id}/runtimes", status_code=201)
def create_runtime(project_id: str, body: RuntimeCreate, request: Request, user: User):
    st = state(request)
    p = _project(st, user, project_id, "admin")
    st.limiters.setdefault("rt_create", RateLimiter(0.2, 5)).check(user.user_id)
    rt = st.store.create_runtime(p["workspace_id"], p["id"], body.name, body.is_mock, user.user_id)
    tok = _issue(st, rt, user, body.expires_days)
    st.store.audit(p["workspace_id"], user.user_id, "runtime.create", "runtime", rt["id"], {"is_mock": body.is_mock})
    return {"runtime": _rt_view(rt, st.clock()), "credential": tok}


@router.post("/runtimes/{runtime_id}/rotate", status_code=201)
def rotate(runtime_id: str, request: Request, user: User, expires_days: int = Query(default=30, ge=1, le=90)):
    """Issue a new credential and retire every earlier one immediately (the connector must be restarted with the new token)."""
    st = state(request)
    rt = _runtime(st, user, runtime_id, "admin")
    old = [c for c in st.store.list_credentials(rt["id"]) if not c.get("revoked_at")]
    new = _issue(st, rt, user, expires_days, rotated_from=old[0]["id"] if old else None)
    for c in old:
        st.store.revoke_credential(c["id"])
    st.store.audit(rt["workspace_id"], user.user_id, "credential.rotate", "runtime", rt["id"], {"retired": len(old)})
    return new


@router.post("/runtimes/{runtime_id}/revoke")
def revoke(runtime_id: str, request: Request, user: User):
    st = state(request)
    rt = _runtime(st, user, runtime_id, "admin")
    n = st.store.revoke_credentials(rt["id"])
    st.store.audit(rt["workspace_id"], user.user_id, "credential.revoke", "runtime", rt["id"], {"count": n})
    return {"revoked": n}


@router.get("/runtimes/{runtime_id}/credentials")
def credentials(runtime_id: str, request: Request, user: User):
    st = state(request)
    rt = _runtime(st, user, runtime_id, "admin")
    return {"items": st.store.list_credentials(rt["id"])}


# ------------------------------------------------------------------ chat
@router.get("/projects/{project_id}/conversations")
def list_conversations(project_id: str, request: Request, user: User):
    st = state(request)
    _project(st, user, project_id)
    return {"items": st.store.list_conversations(project_id)}


@router.post("/projects/{project_id}/conversations", status_code=201)
def create_conversation(project_id: str, body: ConversationCreate, request: Request, user: User):
    st = state(request)
    p = _project(st, user, project_id, "member")
    if body.runtime_id:
        rt = st.store.get_runtime(_uuid(body.runtime_id))
        if not rt or rt["project_id"] != p["id"]:
            raise ApiError(404, "runtime_not_found", "runtime not found")
    return st.store.create_conversation(p["workspace_id"], p["id"], user.user_id, body.title, body.runtime_id)


@router.get("/conversations/{conversation_id}/messages")
def messages(conversation_id: str, request: Request, user: User):
    st = state(request)
    c = _conversation(st, user, conversation_id)
    return {"conversation": c, "items": st.store.list_messages(c["id"])}


@router.post("/conversations/{conversation_id}/jobs", response_model=SubmitResponse, status_code=202)
def submit(conversation_id: str, body: SubmitJob, request: Request, user: User,
           idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]):
    st = state(request)
    c = _conversation(st, user, conversation_id, "member")
    st.limiters.setdefault("submit", RateLimiter(1.0, 5)).check(user.user_id)
    if len(body.content) > st.settings.max_prompt_chars:
        raise ApiError(422, "prompt_too_long", "prompt too long")
    rt = st.store.get_runtime(_uuid(body.runtime_id))
    if not rt or rt["workspace_id"] != c["workspace_id"] or rt["project_id"] != c["project_id"] or rt.get("disabled_at"):
        raise ApiError(404, "runtime_not_found", "runtime not found")
    rs = runtime_status(rt, st.clock())
    if rs["status"] not in ("online", "stale"):
        raise ApiError(409, "runtime_offline", f"runtime is {rs['status'].replace('_', ' ')}", extra={"runtime_status": rs["status"]})
    max_len = rt.get("max_model_len") or 2048
    if body.max_tokens >= max_len // 2:
        raise ApiError(422, "max_tokens_too_large", f"max_tokens must be below half the context window ({max_len})")
    # ~3 chars/token is a deliberately conservative estimate; the real tokenizer limit is enforced by vLLM and surfaces as a job failure.
    char_budget = max(0, (max_len - body.max_tokens) * 3 - 200)
    idem = hashlib.sha256(f"{user.user_id}:{idempotency_key}".encode()).hexdigest()
    res = st.store.submit_job(c["workspace_id"], c["id"], rt["id"], user.user_id, idem, body.content, {"max_tokens": body.max_tokens, "temperature": body.temperature}, char_budget)
    if "error" in res:
        status, code = _SUBMIT_ERR.get(res["error"], (400, res["error"]))
        raise ApiError(status, code, f"job rejected: {code}", extra={k: v for k, v in res.items() if k == "char_budget"})
    st.broadcaster.send(c["id"], "status", {"job_id": res["job_id"], "status": res["status"]})
    return SubmitResponse(job_id=res["job_id"], status=res["status"], idempotent=bool(res.get("idempotent")), user_message_id=res.get("user_message_id"))


def _job(st: AppState, user: UserIdentity, job_id: str, min_role: str = "viewer") -> dict:
    j = st.store.get_job(_uuid(job_id))
    if not j:
        raise ApiError(404, "not_found", "not found")
    require_member(st, user, j["workspace_id"], min_role)
    return j


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, request: Request, user: User):
    st = state(request)
    j = _job(st, user, job_id, "member")
    res = st.store.request_cancel(j["workspace_id"], j["id"])
    if "error" in res:
        raise ApiError(404, "not_found", "not found")
    st.store.audit(j["workspace_id"], user.user_id, "job.cancel", "job", j["id"], {"status": res["status"]})
    st.broadcaster.send(j["conversation_id"], "status", {"job_id": j["id"], "status": res["status"]})
    return {"status": res["status"]}


@router.get("/jobs/{job_id}", response_model=JobView)
def job_view(job_id: str, request: Request, user: User, after_seq: int = Query(default=0, ge=0)):
    """Polling fallback and reconnect replay: events strictly after `after_seq`, in order."""
    st = state(request)
    j = _job(st, user, job_id)
    evs = st.store.list_events(j["id"], after_seq, 500)
    rt = st.store.get_runtime(j["runtime_id"])
    return JobView(job_id=j["id"], status=j["status"], conversation_id=j["conversation_id"], runtime_id=j["runtime_id"], attempt=j["attempt"],
                   created_at=j["created_at"], first_token_at=j["first_token_at"], finished_at=j["finished_at"], error_code=j["error_code"],
                   error_message=j["error_message"], result=j["result"], assistant_message_id=j["assistant_message_id"], last_event_seq=j["last_event_seq"],
                   events=[{"seq": e["event_seq"], "kind": e["kind"], "text": e["text"]} for e in evs],
                   runtime_status=runtime_status(rt, st.clock())["status"] if rt else None)


# ------------------------------------------------------------------ telemetry views
def _window(st: AppState, range_: str) -> tuple[datetime, datetime]:
    if range_ not in RANGES:
        raise ApiError(422, "invalid_range", f"range must be one of {', '.join(RANGES)}")
    now = st.clock()
    return now - timedelta(seconds=RANGES[range_]), now


def _provenance(rows: list[dict]) -> dict:
    mock = sum(1 for r in rows if r.get("is_mock"))
    return {"rows": len(rows), "mock_rows": mock, "measured_rows": len(rows) - mock}


def _scoped_runtime(st: AppState, p: dict, runtime_id: str | None) -> str | None:
    if runtime_id is None:
        return None
    rt = st.store.get_runtime(_uuid(runtime_id))
    if not rt or rt["project_id"] != p["id"]:
        raise ApiError(404, "not_found", "not found")
    return rt["id"]


@router.get("/projects/{project_id}/metrics/gpu")
def gpu_metrics(project_id: str, request: Request, user: User, runtime_id: str | None = None, range: str = "15m", max_points: int = Query(default=240, ge=10, le=600)):
    st = state(request)
    p = _project(st, user, project_id)
    rid = _scoped_runtime(st, p, runtime_id)
    since, until = _window(st, range)
    rows = st.store.query_gpu(p["id"], rid, since, until, MAX_ROWS)
    latest = {}
    for r in rows:
        latest[r["gpu_index"]] = r
    now = st.clock()
    return {"since": since, "until": until, "range": range, "truncated": len(rows) >= MAX_ROWS, "provenance": _provenance(rows),
            "series": {str(k): v for k, v in series.bucket_gpu(rows, since, until, max_points).items()},
            "latest": {str(k): {**{f: r.get(f) for f in series.GPU_FIELDS}, "captured_at": r["captured_at"], "age_s": round((now - aware(r["captured_at"])).total_seconds(), 1),
                                "gpu_uuid": r.get("gpu_uuid"), "is_mock": r.get("is_mock")} for k, r in latest.items()}}


@router.get("/projects/{project_id}/metrics/vllm")
def vllm_metrics(project_id: str, request: Request, user: User, runtime_id: str | None = None, range: str = "15m", max_points: int = Query(default=240, ge=10, le=600)):
    st = state(request)
    p = _project(st, user, project_id)
    rid = _scoped_runtime(st, p, runtime_id)
    since, until = _window(st, range)
    rows = st.store.query_vllm(p["id"], rid, since, until, MAX_ROWS)
    pts = series.downsample(series.vllm_points(rows), max_points)
    last = rows[-1] if rows else None
    now = st.clock()
    return {"since": since, "until": until, "range": range, "truncated": len(rows) >= MAX_ROWS, "provenance": _provenance(rows), "points": pts,
            "latest_age_s": round((now - aware(last["captured_at"])).total_seconds(), 1) if last else None,
            "latest_cumulative": {"ttft": last.get("ttft_hist"), "e2e": last.get("e2e_hist"), "itl": last.get("itl_hist"), "request_success": last.get("request_success")} if last else None}


@router.get("/projects/{project_id}/overview")
def overview(project_id: str, request: Request, user: User):
    st = state(request)
    p = _project(st, user, project_id)
    runtimes = [_rt_view(r, st.clock()) for r in st.store.list_runtimes(p["id"])]
    now = st.clock()
    since = now - timedelta(minutes=5)
    gpu_rows = st.store.query_gpu(p["id"], None, since, now, 2000)
    latest: dict[tuple[str, int], dict] = {}
    for r in gpu_rows:
        latest[(r["runtime_id"], r["gpu_index"])] = r
    vllm_rows = st.store.query_vllm(p["id"], None, since, now, 2000)
    return {"runtimes": runtimes, "provenance": _provenance(gpu_rows + vllm_rows),
            "gpus": [{"runtime_id": k[0], "gpu_index": k[1], **{f: r.get(f) for f in series.GPU_FIELDS}, "captured_at": r["captured_at"], "is_mock": r.get("is_mock"),
                      "age_s": round((now - aware(r["captured_at"])).total_seconds(), 1)} for k, r in sorted(latest.items())],
            "vllm_points": series.downsample(series.vllm_points(vllm_rows), 60)}


@router.get("/projects/{project_id}/runs")
def runs(project_id: str, request: Request, user: User):
    st = state(request)
    p = _project(st, user, project_id)
    return {"items": st.store.list_runs(p["id"])}


@router.get("/runs/{run_id}")
def run_detail(run_id: str, request: Request, user: User):
    st = state(request)
    run = st.store.get_run(_uuid(run_id))
    if not run:
        raise ApiError(404, "not_found", "not found")
    require_member(st, user, run["workspace_id"])
    since = aware(run["started_at"])
    until = aware(run["ended_at"]) or st.clock()
    gpu_rows = [r for r in st.store.query_gpu(run["project_id"], run["runtime_id"], since, until, MAX_ROWS) if r.get("run_id") in (None, run["id"])]
    vllm_rows = [r for r in st.store.query_vllm(run["project_id"], run["runtime_id"], since, until, MAX_ROWS) if r.get("run_id") in (None, run["id"])]
    return {"run": run, "summary": series.run_summary(gpu_rows, vllm_rows), "provenance": _provenance(gpu_rows + vllm_rows)}


# ------------------------------------------------------------------ housekeeping (scheduled one-shot: the .github/workflows/retention.yml GitHub Actions cron calls it daily)
admin = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _require_cron_secret(request: Request, authorization: str | None) -> None:
    secret = state(request).settings.cron_secret
    given = (authorization or "")[7:] if (authorization or "").lower().startswith("bearer ") else ""
    if not secret or not hmac.compare_digest(given.encode(), secret.encode()):
        raise ApiError(401, "unauthorized", "authentication required")


@admin.get("/prune")
def prune(request: Request, authorization: str | None = Header(default=None)):
    _require_cron_secret(request, authorization)
    from datetime import datetime, timezone
    hold = state(request).settings.retention_hold
    held = hold is not None and datetime.now(timezone.utc) < hold
    # During the bounded migration hold finished-job streaming events are kept (event_hours ~10 years); everything else is pruned as usual.
    out = state(request).store.prune(event_hours=87600) if held else state(request).store.prune()
    out = {**(out or {}), "events_retention_hold_until": hold.isoformat() if held else None}
    diag = getattr(request.app.state, "diag", None)
    if diag is not None:
        from datetime import datetime, timezone
        out = {**(out or {}), "diag_events": diag.prune(datetime.now(timezone.utc))}
    return out


@admin.get("/ready")
def ready(request: Request, authorization: str | None = Header(default=None)):
    """Readiness (needs the database), distinct from the public liveness check /api/v1/health. Secret-protected so anonymous callers cannot probe
    or load the database; a database failure is the standard 503 from the StoreUnavailable handler (no detail leaked)."""
    _require_cron_secret(request, authorization)
    st = state(request)
    st.store.ready()
    return {"ready": True, "store": st.settings.store}
