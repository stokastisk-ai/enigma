"""Enigma runtime API on AWS Lambda (Function URL), OPTIONAL transport for Kaggle runtimes. Standard library only on top of the API itself.

It is the SAME FastAPI application as the Railway API (``enigma_api.main.create_app``): same routes, same runtime-token authentication, same tenant
checks, same DynamoStore conditional writes, same body limits. Nothing is re-implemented here; this module only translates a Lambda Function URL
event (payload format 2.0) into one ASGI HTTP request and the response back. No LLM inference ever runs here (Kaggle T4 GPUs only), there is no
proxy to any user-supplied URL, and the Kaggle vLLM port is never exposed.

Configuration comes from the function's environment (ENIGMA_STORE=dynamodb, ENIGMA_DDB_TABLE, ENIGMA_AUTH_PROVIDER=cognito, COGNITO_*, ENIGMA_ENV,
ENIGMA_DEPLOYMENT, ENIGMA_CRON_SECRET). The app is created during the INIT phase so a configuration error fails the cold start, not a request."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

log = logging.getLogger("enigma.lambda")
MAX_RESPONSE = 6 * 1024 * 1024 - 64 * 1024            # Function URL response limit (6 MB) minus headroom

_APP = None
_LOOP: asyncio.AbstractEventLoop | None = None


def get_app():
    global _APP
    if _APP is None:
        from enigma_api.main import create_app
        _APP = create_app()
    return _APP


def _scope(event: dict) -> dict:
    http = event.get("requestContext", {}).get("http", {})
    headers = [(k.lower().encode("latin-1"), str(v).encode("latin-1")) for k, v in (event.get("headers") or {}).items()]
    if event.get("cookies"):
        headers.append((b"cookie", "; ".join(event["cookies"]).encode("latin-1")))
    path = event.get("rawPath") or http.get("path") or "/"
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": http.get("method", "GET").upper(),
            "scheme": "https", "path": path, "raw_path": path.encode(), "root_path": "", "query_string": (event.get("rawQueryString") or "").encode(),
            "headers": headers, "client": (http.get("sourceIp") or "0.0.0.0", 0), "server": ((event.get("headers") or {}).get("host", "lambda"), 443)}


async def _call(app, scope: dict, body: bytes) -> dict:
    sent_request = False
    status, out_headers, chunks = 500, [], []

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)                      # a disconnect is never reported mid-request (the Function URL buffers the whole response)
        return {"type": "http.disconnect"}

    async def send(msg):
        nonlocal status, out_headers
        if msg["type"] == "http.response.start":
            status, out_headers = msg["status"], msg.get("headers", [])
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    await app(scope, receive, send)
    return {"status": status, "headers": out_headers, "body": b"".join(chunks)}


def handle(event: dict, app=None) -> dict:
    global _LOOP
    app = app or get_app()
    raw = event.get("body") or ""
    body = base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode("utf-8")
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    res = _LOOP.run_until_complete(_call(app, _scope(event), body))
    headers: dict[str, str] = {}
    cookies: list[str] = []
    for k, v in res["headers"]:
        name, val = k.decode("latin-1").lower(), v.decode("latin-1")
        if name == "set-cookie":
            cookies.append(val)
        elif name in headers:
            headers[name] += ", " + val
        else:
            headers[name] = val
    data = res["body"]
    if len(data) > MAX_RESPONSE:
        data = json.dumps({"error": {"code": "response_too_large", "message": "response exceeds the transport limit", "correlation_id": "-"}}).encode()
        res["status"], headers = 502, {"content-type": "application/json"}
    ctype = headers.get("content-type", "")
    text = ctype.startswith(("application/json", "text/")) or not data
    out = {"statusCode": res["status"], "headers": headers, "isBase64Encoded": not text,
           "body": data.decode("utf-8") if text else base64.b64encode(data).decode()}
    if cookies:
        out["cookies"] = cookies
    return out


def lambda_handler(event: dict, context: Any) -> dict:
    try:
        return handle(event)
    except Exception as e:                             # noqa: BLE001 - never leak a stack trace or configuration detail to the caller
        print(json.dumps({"runtime_api": "error", "type": type(e).__name__}))
        return {"statusCode": 503, "headers": {"content-type": "application/json", "cache-control": "no-store"},
                "body": json.dumps({"error": {"code": "unavailable", "message": "service unavailable", "correlation_id": "-"}})}


try:                                                   # INIT phase (burst CPU): build the app once per execution environment
    import os as _os
    if _os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        get_app()
except Exception as _e:                                # noqa: BLE001 - surfaced as 503s and one log line, never as a crash loop with details
    print(json.dumps({"runtime_api": "init_error", "type": type(_e).__name__}))
