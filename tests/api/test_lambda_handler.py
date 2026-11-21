"""Lambda entrypoint: HTTP v2 event round-trip through Mangum and the scheduled prune task (in-memory store; no AWS involved)."""
from __future__ import annotations

import importlib
import json
import sys


def _load(monkeypatch):
    for k, v in {"ENIGMA_ENV": "test", "ENIGMA_STORE": "memory", "ENIGMA_DEV_AUTH": "true", "ENIGMA_DEV_JWT_SECRET": "x" * 40}.items():
        monkeypatch.setenv(k, v)
    from enigma_api.settings import get_settings
    get_settings.cache_clear()
    sys.modules.pop("lambda_handler", None)
    return importlib.import_module("lambda_handler")


def _event(path, method="GET", headers=None, body=None):
    return {"version": "2.0", "routeKey": "$default", "rawPath": path, "rawQueryString": "", "headers": headers or {},
            "requestContext": {"http": {"method": method, "path": path, "sourceIp": "127.0.0.1", "protocol": "HTTP/1.1", "userAgent": "t"},
                               "stage": "$default", "requestId": "r", "domainName": "x", "time": "", "timeEpoch": 0, "accountId": "x", "apiId": "x"},
            "body": body, "isBase64Encoded": False}


def test_http_v2_event_round_trip_and_auth_is_enforced(monkeypatch):
    lh = _load(monkeypatch)
    ok = lh.handler(_event("/api/v1/health"), None)
    assert ok["statusCode"] == 200 and json.loads(ok["body"])["ok"] is True
    denied = lh.handler(_event("/api/v1/app/workspaces"), None)
    assert denied["statusCode"] == 401


def test_scheduled_prune_task_runs_without_http(monkeypatch):
    lh = _load(monkeypatch)
    out = lh.handler({"enigma_task": "prune"}, None)
    assert out["ok"] is True


def test_unknown_task_shaped_event_is_not_treated_as_admin(monkeypatch):
    lh = _load(monkeypatch)
    # an HTTP event that smuggles the task key must still go through HTTP routing/auth, never run prune
    ev = _event("/api/v1/app/workspaces", body=json.dumps({"enigma_task": "prune"}))
    ev["enigma_task"] = "prune"
    assert lh.handler(ev, None)["statusCode"] == 401
