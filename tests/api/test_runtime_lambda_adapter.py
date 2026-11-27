"""The optional AWS runtime endpoint = the SAME FastAPI app behind a Lambda Function URL adapter (infra/aws/runtime_api/lambda_app.py).
CPU-only: every runtime operation is driven through fabricated Function URL (payload 2.0) events and must behave exactly as over HTTP."""
from __future__ import annotations

import base64
import importlib.util
import json
import uuid
from pathlib import Path

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from api_helpers import DEV_SECRET, Tenant, sha
from enigma_api.main import create_app
from enigma_api.settings import Settings
from enigma_api.store.dynamodb import DynamoStore
from enigma_api.store.memory import MemoryStore

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lambda_app", ROOT / "infra" / "aws" / "runtime_api" / "lambda_app.py")
LA = importlib.util.module_from_spec(spec)
spec.loader.exec_module(LA)


class LambdaClient:
    """A TestClient-like facade that sends every request through lambda_app.handle() as a Function URL event."""

    def __init__(self, app):
        self.app = app

    def request(self, method, path, json_body=None, headers=None, raw=None, b64=False):
        path, _, qs = path.partition("?")
        body = raw if raw is not None else (json.dumps(json_body) if json_body is not None else "")
        h = {"host": "abc.lambda-url.us-east-1.on.aws", "content-type": "application/json", **{k.lower(): v for k, v in (headers or {}).items()}}
        if body:
            h["content-length"] = str(len(body.encode() if isinstance(body, str) else body))
        ev = {"version": "2.0", "rawPath": path, "rawQueryString": qs, "headers": h,
              "requestContext": {"http": {"method": method, "path": path, "sourceIp": "203.0.113.7"}},
              "body": base64.b64encode(body.encode()).decode() if b64 else body, "isBase64Encoded": b64}
        out = LA.handle(ev, app=self.app)
        assert isinstance(out["body"], str) and set(out) >= {"statusCode", "headers", "body"}
        return out

    def post(self, path, json=None, headers=None):
        return Resp(self.request("POST", path, json, headers))

    def get(self, path, headers=None):
        return Resp(self.request("GET", path, None, headers))


class Resp:
    def __init__(self, out):
        self.status_code, self.headers, self.text = out["statusCode"], out["headers"], out["body"]

    def json(self):
        return json.loads(self.text)


def _app(backend, **over):
    s = Settings(env="test", dev_auth=True, dev_jwt_secret=DEV_SECRET, cron_secret="c" * 32, deployment="aws-staging-isolated", **over)
    return create_app(s, store=backend)


@pytest.fixture(params=["memory", "dynamodb"])
def app(request, monkeypatch):
    if request.param == "memory":
        yield _app(MemoryStore())
        return
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        c = boto3.client("dynamodb", region_name="us-east-1")
        c.create_table(TableName="enigma-rt-staging", BillingMode="PAY_PER_REQUEST",
                       AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}],
                       KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}])
        yield _app(DynamoStore("enigma-rt-staging", client=c), store="dynamodb", ddb_table="enigma-rt-staging")


def test_health_reports_capabilities_and_an_honest_deployment_label(app):
    h = LambdaClient(app).get("/api/v1/health").json()
    assert h["ok"] and h["deployment"] == "aws-staging-isolated" and h["store"] in ("memory", "dynamodb") and "auth" in h


def test_full_runtime_contract_through_the_lambda_adapter(app):
    web = TestClient(app, raise_server_exceptions=False)             # the web side (users) may use either transport: it is the same app and store
    alice = Tenant(web, "user_alice")
    alice.create_runtime(is_mock=False)
    lam = LambdaClient(app)
    reg = lam.post("/api/v1/runtimes/register", {"connector_version": "test", "is_mock": False, "served_model": "Qwen/Qwen2.5-0.5B-Instruct", "tp": 1,
                                                 "pp": 1, "max_model_len": 2048,
                                                 "gpus": [{"index": 0, "uuid": "GPU-X", "name": "Tesla T4", "memory_total_mib": 15360}]}, alice.rh)
    assert reg.status_code == 200, reg.text
    run_id = reg.json()["run_id"]
    assert lam.post(f"/api/v1/runtimes/{alice.runtime_id}/heartbeat", {"run_id": run_id}, alice.rh).status_code == 200
    # a Playground job submitted on the web side is claimed, streamed (ordered, idempotent) and completed via the Lambda transport
    conv = alice.conversation()
    job_id = alice.submit(conv, "hello").json()["job_id"]
    claimed = lam.post("/api/v1/jobs/claim", {}, alice.rh).json()["job"]
    assert claimed["job_id"] == job_id and claimed["next_event_seq"] == 1
    assert lam.post("/api/v1/jobs/claim", {}, alice.rh).json()["job"] is None                       # conditional claim: no double claim
    ev = lambda s, t: {"event_seq": s, "kind": "delta", "text": t}                                    # noqa: E731
    r = lam.post(f"/api/v1/jobs/{job_id}/events", {"events": [ev(1, "Hel"), ev(2, "lo")]}, alice.rh).json()
    assert r["accepted"] == 2 and r["last_event_seq"] == 2
    assert lam.post(f"/api/v1/jobs/{job_id}/events", {"events": [ev(2, "lo")]}, alice.rh).json()["duplicates"] == 1
    assert lam.get(f"/api/v1/jobs/{job_id}/status", alice.rh).json()["cancel_requested"] is False
    done = lam.post(f"/api/v1/jobs/{job_id}/complete", {"final_seq": 2, "text_sha256": sha("Hello")}, alice.rh)
    assert done.status_code == 200 and done.json()["assistant_message_id"]
    msgs = web.get(f"/api/v1/app/conversations/{conv}/messages", headers=alice.h).json()["items"]
    assert [(m["role"], m["content"]) for m in msgs] == [("user", "hello"), ("assistant", "Hello")]


def test_tenant_isolation_and_authentication_are_unchanged(app):
    web = TestClient(app, raise_server_exceptions=False)
    alice, bob = Tenant(web, "user_alice"), Tenant(web, "user_bob")
    alice.create_runtime(); bob.create_runtime()
    alice.register(); bob.register()
    lam = LambdaClient(app)
    job_id = alice.submit(alice.conversation(), "secret").json()["job_id"]
    assert lam.post("/api/v1/jobs/claim", {}, bob.rh).json()["job"] is None                         # bob's runtime never sees alice's job
    assert lam.post(f"/api/v1/jobs/{job_id}/complete", {"final_seq": 0, "text_sha256": sha("")}, bob.rh).status_code == 404
    assert lam.post("/api/v1/jobs/claim", {}, {"Authorization": "Bearer enig_rt_abcd1234_" + "x" * 43}).status_code == 401
    assert lam.post("/api/v1/jobs/claim", {}, {"Authorization": "Bearer edx_abcd1234_" + "x" * 43}).status_code == 401   # a diagnostic key grants nothing
    web.post(f"/api/v1/app/runtimes/{alice.runtime_id}/revoke", headers=alice.h)
    assert lam.post("/api/v1/jobs/claim", {}, alice.rh).status_code == 401                          # revocation applies to the Lambda transport too


def test_body_limits_base64_bodies_and_query_strings(app):
    web = TestClient(app, raise_server_exceptions=False)
    alice = Tenant(web, "user_alice"); alice.create_runtime(); alice.register()
    lam = LambdaClient(app)
    big = json.dumps({"events": [{"event_seq": 1, "kind": "delta", "text": "x" * 3_000_000}]})
    assert lam.request("POST", f"/api/v1/jobs/{uuid.uuid4()}/events", raw=big, headers=alice.rh)["statusCode"] == 413
    out = lam.request("POST", "/api/v1/jobs/claim", raw="{}", headers=alice.rh, b64=True)
    assert out["statusCode"] == 200 and json.loads(out["body"])["job"] is None
    job_id = alice.submit(alice.conversation(), "q").json()["job_id"]
    assert web.get(f"/api/v1/app/jobs/{job_id}?after_seq=0", headers=alice.h).status_code == 200
    assert lam.get(f"/api/v1/app/jobs/{job_id}?after_seq=0", alice.h).status_code == 200


def test_errors_never_leak_internals():
    class Boom:
        async def __call__(self, scope, receive, send):
            raise RuntimeError("postgresql dsn and secret detail (synthetic)")
    LA._APP = Boom()
    try:
        out = LA.lambda_handler({"rawPath": "/api/v1/health", "requestContext": {"http": {"method": "GET"}}}, None)
    finally:
        LA._APP = None
    assert out["statusCode"] == 503 and "secret" not in out["body"] and "postgresql" not in out["body"]


def test_package_excludes_the_inference_stack_and_the_railway_driver():
    src = (ROOT / "infra" / "aws" / "runtime_api" / "build_package.py").read_text()
    for banned in ("torch", "vllm", "stokastisk", "psycopg", "uvicorn"):
        assert f'"{banned}"' in src                                                                  # actively refused by the builder
    la = (ROOT / "infra" / "aws" / "runtime_api" / "lambda_app.py").read_text()
    assert "urlopen" not in la and "httpx" not in la and "requests" not in la                       # no proxying anywhere
