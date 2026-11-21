"""LIVE test against a real Clerk DEVELOPMENT instance (free). Skipped unless ENIGMA_LIVE_CLERK=1 and apps/web/.env.local holds the keys.

Creates two throwaway users via Clerk's Backend API, mints real session JWTs (signed by Clerk's real key, verified through the real
JWKS endpoint), proves the API accepts them and keeps the tenants apart, then deletes the users. No key or token is ever printed."""
from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from enigma_api.main import create_app
from enigma_api.settings import Settings
from enigma_api.store.memory import MemoryStore

ENV = Path(__file__).resolve().parents[2] / "apps" / "web" / ".env.local"
pytestmark = pytest.mark.skipif(os.environ.get("ENIGMA_LIVE_CLERK") != "1" or not ENV.exists(), reason="live Clerk test: set ENIGMA_LIVE_CLERK=1 (needs apps/web/.env.local)")


def load_env() -> dict[str, str]:
    out = {}
    for line in ENV.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


class Clerk:
    def __init__(self, sk: str):
        self.sk = sk

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request("https://api.clerk.com" + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {self.sk}", "Content-Type": "application/json", "User-Agent": "enigma-live-test/0.1"})
        for attempt in range(4):                                   # bounded retry for transient network/TLS timeouts only
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                raise AssertionError(f"Clerk {method} {path} -> HTTP {e.code}") from None      # body deliberately not echoed
            except (urllib.error.URLError, TimeoutError):
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))
        raise AssertionError("unreachable")

    def user_token(self, tag: str) -> tuple[str, str]:
        email = f"enigma-live-{tag}-{secrets.token_hex(4)}+clerk_test@example.com"
        user = self.call("POST", "/v1/users", {"email_address": [email], "password": secrets.token_urlsafe(24), "skip_password_checks": True})
        sess = self.call("POST", "/v1/sessions", {"user_id": user["id"]})
        jwt_ = self.call("POST", f"/v1/sessions/{sess['id']}/tokens", {})["jwt"]
        return user["id"], jwt_


@pytest.fixture(scope="module")
def live():
    env = load_env()
    c = Clerk(env["CLERK_SECRET_KEY"])
    created: list[str] = []
    try:
        a_id, a_tok = c.user_token("a"); created.append(a_id)
        b_id, b_tok = c.user_token("b"); created.append(b_id)
        # No CLERK_AUTHORIZED_PARTIES here: backend-minted tokens have no browser origin (azp). Browser tokens are covered by the UI test.
        s = Settings(env="test", clerk_issuer=env["CLERK_ISSUER"], clerk_jwks_url=env["CLERK_JWKS_URL"])
        app = create_app(s, store=MemoryStore())
        with TestClient(app, raise_server_exceptions=False) as client:
            yield {"client": client, "a": (a_id, a_tok), "b": (b_id, b_tok), "issuer": env["CLERK_ISSUER"]}
    finally:
        for uid in created:
            try:
                c.call("DELETE", f"/v1/users/{uid}")
            except AssertionError:
                pass


def H(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def test_real_clerk_jwt_is_verified_by_real_jwks(live):
    c = live["client"]
    r = c.get("/api/v1/app/me", headers=H(live["a"][1]))
    assert r.status_code == 200 and r.json() == {"user_id": live["a"][0], "auth": "clerk"}


def test_tampered_and_foreign_tokens_rejected(live):
    c = live["client"]
    tok = live["a"][1]
    head, body, sig = tok.split(".")
    assert c.get("/api/v1/app/me", headers=H(f"{head}.{body}.{sig[:-4]}AAAA")).status_code == 401           # corrupted signature
    assert c.get("/api/v1/app/me", headers=H(f"{head}.{live['b'][1].split('.')[1]}.{sig}")).status_code == 401  # A's signature on B's claims
    assert c.get("/api/v1/app/me", headers=H("not.a.jwt")).status_code == 401
    assert c.get("/api/v1/app/me").status_code == 401


def test_two_real_users_are_isolated(live):
    c = live["client"]
    a, b = H(live["a"][1]), H(live["b"][1])
    ws = c.post("/api/v1/app/workspaces", json={"name": "Live A"}, headers=a).json()
    wid, pid = ws["workspace"]["id"], ws["project"]["id"]
    assert c.get(f"/api/v1/app/workspaces/{wid}/projects", headers=a).status_code == 200
    assert c.get("/api/v1/app/workspaces", headers=b).json()["items"] == []
    for url in (f"/api/v1/app/workspaces/{wid}/projects", f"/api/v1/app/projects/{pid}/runtimes", f"/api/v1/app/projects/{pid}/overview"):
        assert c.get(url, headers=b).status_code == 404, url
    assert c.post(f"/api/v1/app/projects/{pid}/runtimes", json={"name": "evil"}, headers=b).status_code == 404


def test_hs256_dev_tokens_not_accepted_in_clerk_mode(live):
    import jwt as pyjwt
    forged = pyjwt.encode({"sub": live["a"][0], "aud": "enigma-dev", "exp": 4102444800}, "x" * 48, algorithm="HS256")
    assert live["client"].get("/api/v1/app/me", headers=H(forged)).status_code == 401


def test_token_claims_look_like_a_real_clerk_session(live):
    import jwt as pyjwt
    claims = pyjwt.decode(live["a"][1], options={"verify_signature": False})
    assert claims["iss"] == live["issuer"] and claims["sub"] == live["a"][0] and "exp" in claims
