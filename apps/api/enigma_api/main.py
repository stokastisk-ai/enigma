from __future__ import annotations

import json
import logging
import re

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .deps import AppState
from .errors import install_error_handlers
from .realtime import NullBroadcaster
from .security import UserVerifier
from .settings import Settings, get_settings
from .store.base import Store

log = logging.getLogger("enigma.api")


class BodyLimitMiddleware:
    """Reject oversized bodies (declared or streamed) before they are parsed. Pure ASGI so it cannot buffer an unbounded body."""

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app, self.s = app, settings

    def _limit(self, path: str) -> int:
        if path.startswith("/api/v1/telemetry"):
            return self.s.max_body_bytes_telemetry
        if path.startswith("/api/v1/app"):
            return self.s.max_body_bytes_app
        return self.s.max_body_bytes_runtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        limit = self._limit(scope["path"])
        declared = next((v for k, v in scope["headers"] if k == b"content-length"), None)
        too_big = False
        try:
            too_big = declared is not None and int(declared) > limit
        except ValueError:
            too_big = True
        started = False

        async def reject():
            body = json.dumps({"error": {"code": "payload_too_large", "message": f"body exceeds {limit} bytes", "correlation_id": "-"}}).encode()
            await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})

        if too_big:
            return await reject()
        seen = 0

        class _Exceeded(BaseException):      # BaseException: framework `except Exception` handlers must not turn this into a 400
            pass

        async def counting_receive():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    raise _Exceeded
            return msg

        async def tracking_send(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _Exceeded:
            if not started:
                await reject()


def create_app(settings: Settings | None = None, store: Store | None = None, broadcaster=None, verifier: UserVerifier | None = None, clock=None) -> FastAPI:
    s = settings or get_settings()
    problems = s.validate_for_startup()
    if problems:
        raise RuntimeError("refusing to start with unsafe configuration: " + "; ".join(problems))
    if store is None:
        if s.store == "dynamodb":
            from .store.dynamodb import DynamoStore
            store = DynamoStore(s.ddb_table)
        elif s.store == "postgres":
            from .store.postgres import PostgresStore
            store = PostgresStore(s.db_dsn)
        else:
            from .store.memory import MemoryStore
            store = MemoryStore()
    if broadcaster is None:
        broadcaster = NullBroadcaster()      # chat updates reach browsers by authenticated polling of inference_events

    docs = not s.is_production
    app = FastAPI(title="Enigma control-plane API", version=__version__, docs_url="/api/v1/docs" if docs else None,
                  redoc_url=None, openapi_url="/api/v1/openapi.json" if docs else None)
    app.state.enigma = AppState(settings=s, store=store, verifier=verifier or UserVerifier(s), broadcaster=broadcaster, **({"clock": clock} if clock else {}))
    app.add_middleware(BodyLimitMiddleware, settings=s)
    install_error_handlers(app)

    from .routes_app import admin as admin_router
    from .routes_app import router as app_router
    from .routes_runtime import router as rt_router
    app.include_router(rt_router)
    app.include_router(app_router)
    app.include_router(admin_router)

    @app.get("/api/v1/health", tags=["meta"])
    def health():
        return {"ok": True, "version": __version__, "store": s.store}

    return app
