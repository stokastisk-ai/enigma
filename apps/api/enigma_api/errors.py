"""One error shape for every failure: {"error": {"code", "message", "correlation_id"}}.

Validation errors deliberately omit the submitted value: request bodies carry prompts and must never be echoed or logged.
"""
from __future__ import annotations

import logging
import re
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("enigma.api")
_CID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict[str, str] | None = None, extra: dict | None = None):
        self.status, self.code, self.message, self.headers, self.extra = status, code, message, headers or {}, extra or {}


def correlation_id(request: Request) -> str:
    cid = getattr(request.state, "correlation_id", None)
    if cid:
        return cid
    incoming = request.headers.get("x-request-id", "")
    cid = incoming if _CID.match(incoming) else uuid.uuid4().hex
    request.state.correlation_id = cid
    return cid


def _body(request: Request, status: int, code: str, message: str, extra: dict | None = None, headers: dict | None = None) -> JSONResponse:
    cid = correlation_id(request)
    payload = {"error": {"code": code, "message": message, "correlation_id": cid, **(extra or {})}}
    return JSONResponse(payload, status_code=status, headers={"x-request-id": cid, **(headers or {})})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError):
        return _body(request, exc.status, exc.code, exc.message, exc.extra, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields = [{"loc": [str(p) for p in e.get("loc", ())], "type": e.get("type", "invalid")} for e in exc.errors()[:20]]
        return _body(request, 422, "validation_error", "request validation failed", {"fields": fields})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return _body(request, exc.status_code, "http_error" if exc.status_code != 404 else "not_found", str(exc.detail))

    from .store.supabase import StoreUnavailable

    @app.exception_handler(StoreUnavailable)
    async def _store_down(request: Request, exc: StoreUnavailable):
        log.error("store unavailable cid=%s path=%s", correlation_id(request), request.url.path)
        return _body(request, 503, "store_unavailable", "database temporarily unavailable", headers={"retry-after": "5"})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # log the exception type + correlation id only: messages can embed request content
        log.error("unhandled %s cid=%s path=%s", type(exc).__name__, correlation_id(request), request.url.path)
        return _body(request, 500, "internal_error", "internal error")
