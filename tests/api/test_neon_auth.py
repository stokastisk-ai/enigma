"""Neon Managed Better Auth JWT verification (unit/integration with LOCALLY generated Ed25519 keys).

Proves claim handling, provider coexistence and tenant isolation in OUR code. It is NOT evidence about the real Neon Auth service (token issuance,
JWKS hosting, sign-up flows): that is exercised separately against the live branch (docs/ENIGMA_RAILWAY_PRODUCTION_AUTH_REPORT.md).
"""
from __future__ import annotations

import base64
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from fastapi.testclient import TestClient

from api_helpers import Clock
from enigma_api.errors import ApiError
from enigma_api.main import create_app
from enigma_api.realtime import RecordingBroadcaster
from enigma_api.security import UserVerifier
from enigma_api.settings import Settings
from enigma_api.store.memory import MemoryStore

BASE = "https://ep-test.neonauth.us-east-2.aws.neon.tech/neondb/auth"
ORIGIN = "https://ep-test.neonauth.us-east-2.aws.neon.tech"
KID = "kid-1"
_KEY = ed25519.Ed25519PrivateKey.generate()
_OTHER = ed25519.Ed25519PrivateKey.generate()
_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
COGNITO = dict(auth_provider="cognito", cognito_region="us-east-1", cognito_user_pool_id="us-east-1_TESTPOOL", cognito_app_client_id="testclientid123")


def settings(**kw) -> Settings:
    return Settings(**{**dict(env="test", cron_secret="c" * 32, neon_auth_base_url=BASE, **COGNITO), **kw})


def mint(sub="3f0f3f0e-0000-4000-8000-000000000001", *, key=_KEY, iss=ORIGIN, aud=ORIGIN, exp_in=600, verified=True, banned=False, alg="EdDSA", drop=(), kid=KID):
    claims = {"sub": sub, "id": sub, "iss": iss, "aud": aud, "iat": int(time.time()), "exp": int(time.time()) + exp_in,
              "email": "x@example.test", "emailVerified": verified, "banned": banned, "role": "authenticated"}
    for d in drop:
        claims.pop(d, None)
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def verifier(**kw) -> UserVerifier:
    return UserVerifier(settings(**kw), key_resolver=lambda _t: _KEY.public_key())


def test_valid_token_namespaces_the_subject():
    ident = verifier().verify(mint("abc-123"))
    assert ident.user_id == "neon:abc-123" and ident.auth == "neon"


@pytest.mark.parametrize("tok", [
    mint(iss="https://ep-other.neonauth.us-east-2.aws.neon.tech"),               # another Neon project/branch
    mint(aud="https://ep-other.neonauth.us-east-2.aws.neon.tech"),               # token minted for another audience
    mint(iss=BASE),                                                              # full URL instead of origin
    mint(exp_in=-5),                                                             # expired
    mint(key=_OTHER),                                                            # unknown signing key
    mint(verified=False),                                                        # e-mail not verified
    mint(banned=True),                                                           # banned account
    mint(drop=("emailVerified",)),
    mint(drop=("exp",)),
    mint(drop=("iss",)),
    mint(drop=("aud",)),
    mint(drop=("sub",)),
    mint(sub="a:b"),                                                             # ':' could forge a namespace prefix
    mint(sub=""),
])
def test_bad_tokens_are_rejected(tok):
    with pytest.raises(ApiError) as e:
        verifier().verify(tok)
    assert e.value.status == 401


def test_unverified_email_allowed_only_when_explicitly_configured():
    v = verifier(neon_require_verified_email=False)
    assert v.verify(mint(verified=False)).user_id.startswith("neon:")


def test_not_configured_fails_closed():
    v = UserVerifier(settings(neon_auth_base_url=""), key_resolver=lambda _t: _KEY.public_key())
    with pytest.raises(ApiError):
        v.verify(mint())


def test_http_auth_url_is_not_trusted_and_is_a_startup_problem():
    s = settings(neon_auth_base_url="http://ep-test.example/neondb/auth")
    assert s.neon_origin == ""
    assert any("NEON_AUTH_BASE_URL" in p for p in s.validate_for_startup())
    with pytest.raises(ApiError):
        UserVerifier(s, key_resolver=lambda _t: _KEY.public_key()).verify(mint())
    assert any("same origin" in p for p in settings(neon_auth_jwks_url="https://evil.example/jwks.json").validate_for_startup())


@pytest.mark.parametrize("alg,key", [("RS256", _RSA), ("HS256", "s" * 40)])
def test_other_algorithms_cannot_pass_as_neon(alg, key):
    forged = jwt.encode({"sub": "u", "iss": ORIGIN, "aud": ORIGIN, "iat": int(time.time()), "exp": int(time.time()) + 60, "emailVerified": True}, key, algorithm=alg)
    with pytest.raises(ApiError):
        verifier().verify(forged)


def test_alg_none_is_refused():
    tok = jwt.encode({"sub": "u", "iss": ORIGIN, "aud": ORIGIN, "exp": int(time.time()) + 60}, None, algorithm="none")
    with pytest.raises(ApiError):
        verifier().verify(tok)


def test_cognito_tokens_still_verify_while_neon_is_configured():
    cog_key = _RSA
    claims = {"sub": "u-1", "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TESTPOOL", "iat": int(time.time()), "exp": int(time.time()) + 600,
              "token_use": "access", "client_id": "testclientid123"}
    tok = jwt.encode(claims, cog_key, algorithm="RS256")
    v = UserVerifier(settings(), key_resolver=lambda t: _KEY.public_key() if jwt.get_unverified_header(t)["alg"] == "EdDSA" else cog_key.public_key())
    assert v.verify(tok).user_id == "cognito:u-1"
    assert v.verify(mint("n-1")).user_id == "neon:n-1"


def test_real_jwks_document_path_with_key_rotation_and_fail_closed():
    """No key_resolver: keys come from a (fake-fetched) JWKS document exactly as production reads <auth url>/.well-known/jwks.json."""
    def jwk(priv, kid):
        raw = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return {"kty": "OKP", "crv": "Ed25519", "x": base64.urlsafe_b64encode(raw).rstrip(b"=").decode(), "kid": kid, "alg": "EdDSA", "use": "sig"}
    docs = [{"keys": [jwk(_KEY, KID)]}]
    urls: list[str] = []
    clock = [1000.0]

    def fetch(url):
        urls.append(url)
        return docs[-1]
    v = UserVerifier(settings(), jwks_fetch=fetch, clock=lambda: clock[0])
    assert v.verify(mint("a")).user_id == "neon:a"
    assert urls == [BASE + "/.well-known/jwks.json"]
    v.verify(mint("b"))
    assert len(urls) == 1                                                         # cached
    with pytest.raises(ApiError):                                                 # unknown kid right after a fetch: refused, and the refresh is throttled
        v.verify(mint("c", key=_OTHER, kid="kid-2"))
    assert len(urls) == 1
    clock[0] += 400
    docs.append({"keys": [jwk(_KEY, KID), jwk(_OTHER, "kid-2")]})                 # operator-side rotation: new key published
    assert v.verify(mint("d", key=_OTHER, kid="kid-2")).user_id == "neon:d"
    assert len(urls) == 2


def test_unreachable_jwks_fails_closed():
    def down(url):
        raise OSError("unreachable")
    with pytest.raises(ApiError):
        UserVerifier(settings(), jwks_fetch=down).verify(mint())


def _app():
    clock = Clock()
    return create_app(settings(), store=MemoryStore(clock), broadcaster=RecordingBroadcaster(), clock=clock, verifier=verifier())


def test_neon_and_cognito_users_with_the_same_email_are_distinct_tenants():
    """No identity merging by e-mail: a Neon user claiming the Cognito user's address sees nothing of the Cognito user's data."""
    cog_claims = {"sub": "cog-1", "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TESTPOOL", "iat": int(time.time()), "exp": int(time.time()) + 600,
                  "token_use": "access", "client_id": "testclientid123", "email": "same@example.test"}
    cog = jwt.encode(cog_claims, _RSA, algorithm="RS256")
    v = UserVerifier(settings(), key_resolver=lambda t: _KEY.public_key() if jwt.get_unverified_header(t)["alg"] == "EdDSA" else _RSA.public_key())
    clock = Clock()
    app = create_app(settings(), store=MemoryStore(clock), broadcaster=RecordingBroadcaster(), clock=clock, verifier=v)
    with TestClient(app, raise_server_exceptions=False) as c:
        h_cog, h_neon = {"Authorization": f"Bearer {cog}"}, {"Authorization": f"Bearer {mint('neon-user', )}"}
        r = c.post("/api/v1/app/workspaces", json={"name": "Cognito WS"}, headers=h_cog)
        assert r.status_code == 201, r.text
        proj = r.json()["project"]["id"]
        assert c.get("/api/v1/app/me", headers=h_neon).json()["user_id"] == "neon:neon-user"
        assert c.get("/api/v1/app/workspaces", headers=h_neon).json()["items"] == []
        assert c.get(f"/api/v1/app/projects/{proj}/runtimes", headers=h_neon).status_code == 404
        assert c.get(f"/api/v1/app/projects/{proj}/runtimes", headers=h_cog).status_code == 200


def test_neon_user_is_never_admin_unless_listed():
    app = create_app(settings(admin_subjects="cognito:someone"), store=MemoryStore(Clock()), broadcaster=RecordingBroadcaster(), clock=Clock(), verifier=verifier())
    with TestClient(app, raise_server_exceptions=False) as c:
        h = {"Authorization": f"Bearer {mint('n')}"}
        assert c.get("/api/v1/app/me", headers=h).json()["is_admin"] is False
        assert c.get("/api/v1/app/me").status_code == 401
        assert c.get("/api/v1/app/me", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_runtime_tokens_are_independent_of_browser_auth():
    """A Neon user token is not a runtime credential and a runtime token is not a user token (Kaggle connector keys stay separate)."""
    app = _app()
    with TestClient(app, raise_server_exceptions=False) as c:
        h = {"Authorization": f"Bearer {mint('n2')}"}
        assert c.post("/api/v1/runtimes/00000000-0000-0000-0000-000000000000/heartbeat", json={}, headers=h).status_code == 401
        assert c.get("/api/v1/app/me", headers={"Authorization": "Bearer enig_rt_abcdefgh_" + "A" * 43}).status_code == 401
