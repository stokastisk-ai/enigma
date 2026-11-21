"""Enigma-admin-only AWS operations endpoints. DEFAULT DENY: only users listed in ENIGMA_ADMIN_SUBJECTS get through; everyone else 403
(anonymous 401 from the shared user dependency). Read-only: no route here can create, change or delete anything, and no AWS id comes from the request."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from .deps import state, user_identity
from .errors import ApiError
from .ratelimit import RateLimiter
from .security import UserIdentity

router = APIRouter(prefix="/api/v1/app/admin/aws", tags=["admin-aws"])


def is_admin(request: Request, user: UserIdentity) -> bool:
    allowed = {s.strip() for s in state(request).settings.admin_subjects.split(",") if s.strip()}
    return user.user_id in allowed


def require_admin(request: Request, user: Annotated[UserIdentity, Depends(user_identity)]) -> UserIdentity:
    if not is_admin(request, user):
        raise ApiError(403, "forbidden", "administrator access required")
    state(request).limiters.setdefault("aws_ops", RateLimiter(1.0, 30)).check(user.user_id)      # AWS reads are TTL-cached anyway; this only bounds API load (one page view = 2 calls)
    return user


Admin = Annotated[UserIdentity, Depends(require_admin)]


@router.get("/status")
def aws_status(request: Request, _: Admin):
    return state(request).ops.status()


@router.get("/metrics")
def aws_metrics(request: Request, _: Admin, range: Annotated[str, Query(pattern=r"^(1h|6h|24h)$")] = "1h"):
    return state(request).ops.metrics(range)


@router.get("/cost")
def aws_cost(request: Request, _: Admin):
    return state(request).ops.cost()
