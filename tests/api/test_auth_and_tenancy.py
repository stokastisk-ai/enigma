"""Auth positive/negative, token scope, cross-tenant isolation, production secret checks."""
from __future__ import annotations

import time
from datetime import timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from enigma_api.main import create_app
from enigma_api.security import UserVerifier
from enigma_api.settings import Settings
from enigma_api.store.memory import MemoryStore

from api_helpers import DEV_SECRET, Tenant, dev_token


# ------------------------------------------------------------------ user plane
def test_user_routes_require_auth(client):
    assert client.get("/api/v1/app/me").status_code == 401
    assert client.get("/api/v1/app/me", headers={"Authorization": "Bearer junk"}).status_code == 401
    assert client.get("/api/v1/app/me", headers={"Authorization": f"Bearer {dev_token('u1')}"}).json() == {"user_id": "u1", "auth": "dev", "is_admin": False}   # is_admin: ENIGMA_ADMIN_SUBJECTS is empty => nobody is admin


def test_expired_and_wrong_secret_user_tokens_rejected(client):
    assert client.get("/api/v1/app/me", headers={"Authorization": f"Bearer {dev_token('u1', exp_in=-10)}"}).status_code == 401
    assert client.get("/api/v1/app/me", headers={"Authorization": f"Bearer {dev_token('u1', secret='x' * 48)}"}).status_code == 401


def test_hs256_rejected_when_dev_auth_off(store, bus, clock):
    app = create_app(Settings(env="test", dev_auth=False), store=store, broadcaster=bus, clock=clock)
    with TestClient(app) as c:
        assert c.get("/api/v1/app/me", headers={"Authorization": f"Bearer {dev_token('u1')}"}).status_code == 401


def test_clerk_rs256_path_and_alg_none_rejected(store, bus, clock):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    s = Settings(env="test", clerk_issuer="https://clerk.example.test", clerk_authorized_parties="https://app.example.test")
    app = create_app(s, store=store, broadcaster=bus, clock=clock, verifier=UserVerifier(s, key_resolver=lambda t: key.public_key()))
    now = int(time.time())
    good = {"sub": "user_clerk_1", "iss": "https://clerk.example.test", "iat": now, "exp": now + 60, "azp": "https://app.example.test"}
    with TestClient(app) as c:
        h = lambda t: {"Authorization": f"Bearer {t}"}                       # noqa: E731
        assert c.get("/api/v1/app/me", headers=h(jwt.encode(good, key, algorithm="RS256"))).json()["auth"] == "clerk"
        assert c.get("/api/v1/app/me", headers=h(jwt.encode({**good, "iss": "https://evil.test"}, key, algorithm="RS256"))).status_code == 401
        assert c.get("/api/v1/app/me", headers=h(jwt.encode({**good, "azp": "https://evil.test"}, key, algorithm="RS256"))).status_code == 401
        assert c.get("/api/v1/app/me", headers=h(jwt.encode({**good, "exp": now - 5}, key, algorithm="RS256"))).status_code == 401
        assert c.get("/api/v1/app/me", headers=h(jwt.encode({**good, "sts": "pending"}, key, algorithm="RS256"))).status_code == 401
        # signed with a different key: the resolver returns the trusted public key, so the signature must fail
        assert c.get("/api/v1/app/me", headers=h(jwt.encode(good, other, algorithm="RS256"))).status_code == 401
        none_tok = jwt.encode(good, key=None, algorithm="none")
        assert c.get("/api/v1/app/me", headers=h(none_tok)).status_code == 401


def test_cross_tenant_reads_and_writes_blocked(client, alice, bob):
    a_conv = alice.conversation()
    a_job = alice.submit(a_conv).json()["job_id"]
    # Bob targets every Alice resource: all 404 (existence is not leaked)
    for url in (f"/api/v1/app/conversations/{a_conv}/messages", f"/api/v1/app/jobs/{a_job}", f"/api/v1/app/projects/{alice.project_id}/runtimes",
                f"/api/v1/app/projects/{alice.project_id}/metrics/gpu", f"/api/v1/app/projects/{alice.project_id}/overview",
                f"/api/v1/app/workspaces/{alice.workspace_id}/projects", f"/api/v1/app/runtimes/{alice.runtime_id}/credentials",
                f"/api/v1/app/projects/{alice.project_id}/runs", f"/api/v1/app/projects/{alice.project_id}/conversations"):
        r = client.get(url, headers=bob.h)
        assert r.status_code == 404, url
        assert r.json()["error"]["code"] == "not_found"
    assert client.post(f"/api/v1/app/jobs/{a_job}/cancel", headers=bob.h).status_code == 404
    assert client.post(f"/api/v1/app/runtimes/{alice.runtime_id}/rotate", headers=bob.h).status_code == 404
    assert client.post(f"/api/v1/app/runtimes/{alice.runtime_id}/revoke", headers=bob.h).status_code == 404
    assert client.post(f"/api/v1/app/projects/{alice.project_id}/runtimes", json={"name": "x"}, headers=bob.h).status_code == 404
    assert bob.submit(a_conv).status_code == 404
    # Bob cannot point his own conversation at Alice's runtime
    b_conv = bob.conversation()
    r = client.post(f"/api/v1/app/conversations/{b_conv}/jobs", json={"runtime_id": alice.runtime_id, "content": "x"}, headers={**bob.h, "Idempotency-Key": "k" * 12})
    assert r.status_code == 404 and r.json()["error"]["code"] == "runtime_not_found"
    # Alice's data is untouched
    assert client.get(f"/api/v1/app/jobs/{a_job}", headers=alice.h).json()["status"] == "pending"


def test_malformed_ids_are_404_not_500(client, alice):
    for bad in ("not-a-uuid", "../etc/passwd", "1" * 36 + "x"):
        assert client.get(f"/api/v1/app/jobs/{bad}", headers=alice.h).status_code == 404


def test_viewer_cannot_write_member_cannot_admin(client, store, alice):
    store.add_member(alice.workspace_id, "user_viewer", "viewer")
    store.add_member(alice.workspace_id, "user_member", "member")
    viewer = {"Authorization": f"Bearer {dev_token('user_viewer')}"}
    member = {"Authorization": f"Bearer {dev_token('user_member')}"}
    assert client.get(f"/api/v1/app/projects/{alice.project_id}/runtimes", headers=viewer).status_code == 200
    assert client.post(f"/api/v1/app/projects/{alice.project_id}/conversations", json={}, headers=viewer).status_code == 403
    assert client.post(f"/api/v1/app/projects/{alice.project_id}/conversations", json={}, headers=member).status_code == 201
    assert client.post(f"/api/v1/app/projects/{alice.project_id}/runtimes", json={"name": "x"}, headers=member).status_code == 403
    assert client.get(f"/api/v1/app/runtimes/{alice.runtime_id}/credentials", headers=member).status_code == 403


# ------------------------------------------------------------------ runtime plane / tokens
def test_runtime_token_required_and_scoped(client, alice, bob):
    assert client.post("/api/v1/jobs/claim", json={}).status_code == 401
    assert client.post("/api/v1/jobs/claim", json={}, headers={"Authorization": "Bearer enig_rt_abcd1234_" + "x" * 43}).status_code == 401
    # a user JWT is not a runtime token and vice versa
    assert client.post("/api/v1/jobs/claim", json={}, headers=alice.h).status_code == 401
    assert client.get("/api/v1/app/me", headers=alice.rh).status_code == 401
    # a runtime token can only address its own runtime
    assert client.post(f"/api/v1/runtimes/{bob.runtime_id}/heartbeat", headers=alice.rh).status_code == 404
    assert client.post(f"/api/v1/runtimes/{alice.runtime_id}/heartbeat", headers=alice.rh).status_code == 200


def test_token_hash_only_stored_and_never_listed(client, store, alice):
    creds = client.get(f"/api/v1/app/runtimes/{alice.runtime_id}/credentials", headers=alice.h).json()["items"]
    assert len(creds) == 1 and "secret_hash" not in creds[0]
    secret = alice.token.split("_", 3)[3]
    assert all(secret not in str(v) for c in store.credentials.values() for v in c.values())
    assert secret not in client.get(f"/api/v1/app/projects/{alice.project_id}/runtimes", headers=alice.h).text


def test_rotation_revokes_old_token_immediately(client, alice):
    old = alice.rh
    r = client.post(f"/api/v1/app/runtimes/{alice.runtime_id}/rotate", headers=alice.h)
    assert r.status_code == 201
    assert client.post("/api/v1/jobs/claim", json={}, headers=old).status_code == 401
    assert client.post("/api/v1/jobs/claim", json={}, headers={"Authorization": f"Bearer {r.json()['token']}"}).status_code == 200


def test_revocation_and_expiry(client, clock, alice):
    assert client.post("/api/v1/jobs/claim", json={}, headers=alice.rh).status_code == 200
    assert client.post(f"/api/v1/app/runtimes/{alice.runtime_id}/revoke", headers=alice.h).json()["revoked"] == 1
    assert client.post("/api/v1/jobs/claim", json={}, headers=alice.rh).status_code == 401
    # expiry
    alice.create_runtime("short")
    assert client.post("/api/v1/jobs/claim", json={}, headers=alice.rh).status_code == 200
    clock.advance(31 * 86400)
    assert client.post("/api/v1/jobs/claim", json={}, headers=alice.rh).status_code == 401


def test_mock_credential_cannot_register_as_real_and_vice_versa(client, alice):
    r = client.post("/api/v1/runtimes/register", json={"connector_version": "t", "is_mock": False, "served_model": "m"}, headers=alice.rh)
    assert r.status_code == 409 and r.json()["error"]["code"] == "mock_mismatch"


# ------------------------------------------------------------------ errors, caps, config safety
def test_error_shape_and_no_prompt_echo(client, alice):
    conv = alice.conversation()
    secret_prompt = "TOP-SECRET-PROMPT-" + "x" * 20000
    r = alice.submit(conv, content=secret_prompt)
    assert r.status_code in (413, 422)
    assert "TOP-SECRET-PROMPT" not in r.text
    r = client.post(f"/api/v1/app/conversations/{conv}/jobs", json={"runtime_id": 5, "content": "TOP-SECRET-PROMPT"}, headers={**alice.h, "Idempotency-Key": "k" * 12})
    assert r.status_code == 422 and "TOP-SECRET-PROMPT" not in r.text
    body = r.json()["error"]
    assert body["code"] == "validation_error" and body["correlation_id"] and r.headers["x-request-id"] == body["correlation_id"]


def test_correlation_id_echoed_when_valid(client):
    r = client.get("/api/v1/health", headers={"x-request-id": "abc-1234567890"})
    assert client.get("/api/v1/app/me", headers={"x-request-id": "abc-1234567890"}).headers["x-request-id"] == "abc-1234567890"
    assert client.get("/api/v1/app/me", headers={"x-request-id": "bad id with spaces"}).headers["x-request-id"] != "bad id with spaces"
    assert r.status_code == 200


@pytest.mark.parametrize("kwargs,expect", [
    ({"env": "production", "store": "memory"}, "ENIGMA_STORE"),
    ({"env": "production", "store": "supabase"}, "SUPABASE_URL"),
    ({"env": "production", "store": "supabase", "supabase_url": "https://x.supabase.co", "supabase_service_role_key": "k"}, "CLERK_JWKS_URL"),
    ({"env": "production", "dev_auth": True, "dev_jwt_secret": "d" * 40}, "DEV_AUTH"),
    ({"env": "development", "dev_auth": True, "dev_jwt_secret": "short"}, "32 characters"),
    ({"env": "staging", "dev_auth": True, "dev_jwt_secret": "d" * 40}, "DEV_AUTH"),
])
def test_production_refuses_unsafe_config(kwargs, expect):
    with pytest.raises(RuntimeError) as e:
        create_app(Settings(**kwargs))
    assert expect in str(e.value)


def test_vercel_env_blocks_dev_auth(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    with pytest.raises(RuntimeError, match="DEV_AUTH"):
        create_app(Settings(env="development", dev_auth=True, dev_jwt_secret="d" * 40))


def test_docs_hidden_in_production_config(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    app = create_app(Settings(env="development", store="supabase", supabase_url="https://x.supabase.co", supabase_service_role_key="k",
                              clerk_jwks_url="https://c/.well-known/jwks.json", clerk_issuer="https://c", cron_secret="c" * 32))
    with TestClient(app) as c:
        assert c.get("/api/v1/docs").status_code == 404 and c.get("/api/v1/openapi.json").status_code == 404


def test_body_size_cap_declared_and_streamed(client, alice):
    big = "a" * (300 * 1024)
    r = client.post("/api/v1/jobs/claim", content=big, headers={**alice.rh, "content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"

    def chunks():                                  # no Content-Length: streamed body must still be cut off
        for _ in range(40):
            yield b"a" * 10_000
    r = client.post("/api/v1/jobs/claim", content=chunks(), headers={**alice.rh, "content-type": "application/json"})
    assert r.status_code == 413


def test_rate_limit_returns_429_with_retry_after(client, alice):
    codes = [client.post("/api/v1/jobs/claim", json={}, headers=alice.rh).status_code for _ in range(14)]
    assert 429 in codes and codes[0] == 200
    r = client.post("/api/v1/jobs/claim", json={}, headers=alice.rh)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_prune_requires_cron_secret(client):
    assert client.get("/api/v1/admin/prune").status_code == 401
    assert client.get("/api/v1/admin/prune", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert "events" in client.get("/api/v1/admin/prune", headers={"Authorization": "Bearer " + "c" * 32}).json()
