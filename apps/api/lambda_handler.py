"""AWS Lambda entrypoint (Python 3.13) for the Enigma control-plane API.

Works behind an API Gateway HTTP API or a Lambda Function URL (both use payload format 2.0, which Mangum auto-detects).
A Function URL with auth NONE is publicly callable regardless of CORS: all endpoints already require a verified user JWT or a hashed
runtime token, but treat the URL as public and keep rate/body limits on. NOT deployed -- see docs/AWS_DEPLOYMENT.md (approval gate).

Also accepts a direct EventBridge Scheduler invocation ``{"enigma_task": "prune"}`` for bounded retention without an HTTP cron secret.
Long-running work is never done here: each invocation is one short request (no workers, no held-open streams).
"""
from __future__ import annotations

import logging

from mangum import Mangum

from enigma_api.main import create_app

log = logging.getLogger("enigma.lambda")
_app = create_app()                       # built once per execution environment; refuses to start on unsafe production config
_http = Mangum(_app, lifespan="off")


def handler(event, context):
    if isinstance(event, dict) and event.get("enigma_task") == "prune" and "requestContext" not in event:
        res = _app.state.enigma.store.prune()
        log.info("prune finished")
        return {"ok": True, "pruned": res}
    return _http(event, context)
