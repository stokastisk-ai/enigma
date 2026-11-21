from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import Depends, Header, Request

from .errors import ApiError
from .ratelimit import RateLimiter
from .realtime import Broadcaster
from .security import RuntimeIdentity, UserIdentity, UserVerifier, verify_runtime_token
from .settings import RUNTIME_ONLINE_S, RUNTIME_STALE_S, Settings
from .store.base import Store

_INSTANCE = re.compile(r"^[A-Za-z0-9._-]{1,48}$")


@dataclass
class AppState:
    settings: Settings
    store: Store
    verifier: UserVerifier
    broadcaster: Broadcaster
    limiters: dict[str, RateLimiter] = field(default_factory=dict)
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def state(request: Request) -> AppState:
    return request.app.state.enigma


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "unauthorized", "authentication required", headers={"www-authenticate": "Bearer"})
    return authorization[7:].strip()


def runtime_identity(request: Request, authorization: str | None = Header(default=None)) -> RuntimeIdentity:
    st = state(request)
    ident = verify_runtime_token(st.store, _bearer(authorization), st.clock())
    request.state.runtime = ident
    return ident


def lease_owner(ident: RuntimeIdentity = Depends(runtime_identity), x_enigma_instance: str | None = Header(default=None)) -> str:
    inst = x_enigma_instance or "default"
    if not _INSTANCE.match(inst):
        raise ApiError(400, "bad_instance", "invalid X-Enigma-Instance header")
    return f"{ident.runtime_id}:{inst}"


def user_identity(request: Request, authorization: str | None = Header(default=None)) -> UserIdentity:
    st = state(request)
    return st.verifier.verify(_bearer(authorization))


def require_member(st: AppState, user: UserIdentity, workspace_id: str, min_role: str = "viewer") -> str:
    """404 (not 403) for non-members so tenant ids cannot be probed; 403 only when the caller is a member with too little role."""
    from .store.memory import ROLE_RANK
    role = st.store.get_role(user.user_id, workspace_id)
    if role is None:
        raise ApiError(404, "not_found", "not found")
    if ROLE_RANK[role] < ROLE_RANK[min_role]:
        raise ApiError(403, "forbidden", f"requires {min_role} role")
    return role


def aware(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def runtime_status(rt: dict, now: datetime | None = None) -> dict:
    """Derived from the last heartbeat. 'never_connected' is distinct from 'offline'."""
    now = now or datetime.now(timezone.utc)
    hb = aware(rt.get("last_heartbeat_at"))
    if hb is None:
        return {"status": "never_connected", "heartbeat_age_s": None}
    age = max((now - hb).total_seconds(), 0.0)
    status = "online" if age <= RUNTIME_ONLINE_S else "stale" if age <= RUNTIME_STALE_S else "offline"
    return {"status": status, "heartbeat_age_s": round(age, 1)}
