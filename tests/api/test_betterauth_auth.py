"""Self-hosted Better Auth JWT verification (ENIGMA_AUTH_PROVIDER=betterauth) with LOCALLY generated Ed25519 keys.

Proves claim handling, issuer/audience pinning, JWKS URL policy, provider isolation and tenant separation in OUR code. It is NOT evidence about the
deployed web tier issuing tokens; that is exercised by the live OCI acceptance run (docs/OCI_DEPLOYMENT_REPORT.md).
"""
from __future__ import annotations

import base64
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from enigma_api.errors import ApiError
from enigma_api.security import JwksCache, UserVerifier
from enigma_api.settings import Settings

ISSUER = "https://enigma.example.test"
JWKS = "http://web:3000/api/auth/jwks"
KID = "ba-kid-1"
_KEY = ed25519.Ed25519PrivateKey.generate()
_OTHER = ed25519.Ed25519PrivateKey.generate()
_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
UID = "3f0f3f0e-0000-4000-8000-000000000001"


def settings(**kw) -> Settings:
    return Settings(**{**dict(env="test", cron_secret="c" * 32, auth_provider="betterauth", auth_issuer=ISSUER, auth_jwks_url=JWKS), **kw})


def mint(sub=UID, *, key=_KEY, iss=ISSUER, aud=ISSUER, exp_in=600, verified=True, banned=None, alg="EdDSA", drop=(), kid=KID):
    claims = {"sub": sub, "iss": iss, "aud": aud, "iat": int(time.time()), "exp": int(time.time()) + exp_in, "email": "x@example.test", "emailVerified": verified}
    if banned is not None:
        claims["banned"] = banned
    for d in drop:
        claims.pop(d, None)
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def verifier(**kw) -> UserVerifier:
    return UserVerifier(settings(**kw), key_resolver=lambda _t: _KEY.public_key())


def jwk_of(key: ed25519.Ed25519PrivateKey, kid: str) -> dict:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "kid": kid, "x": base64.urlsafe_b64encode(raw).rstrip(b"=").decode()}


def test_valid_token_is_namespaced_ba():
    ident = verifier().verify(mint())
    assert ident.user_id == f"ba:{UID}" and ident.auth == "betterauth"


@pytest.mark.parametrize("tok", [
    mint(iss="https://other.example.test"),          # another deployment
    mint(aud="https://other.example.test"),
    mint(iss=ISSUER + "/api/auth"),                   # path instead of origin
    mint(exp_in=-5),
    mint(key=_OTHER),
    mint(verified=False),
    mint(banned=True),
    mint(sub="neon:abc"),                             # cannot forge a namespace
    mint(sub=""),
    mint(drop=("aud",)),
    mint(drop=("iat",)),
])
def test_bad_tokens_are_rejected(tok):
    with pytest.raises(ApiError) as e:
        verifier().verify(tok)
    assert e.value.status == 401


def test_unverified_email_only_when_explicitly_relaxed_outside_production():
    assert verifier(auth_require_verified_email=False).verify(mint(verified=False)).user_id == f"ba:{UID}"
    s = settings(env="production", auth_require_verified_email=False, store="postgres", db_dsn="postgresql://u@h/d?sslmode=verify-full")
    assert any("VERIFIED_EMAIL" in p for p in s.validate_for_startup())


@pytest.mark.parametrize("issuer,jwks,ok", [
    (ISSUER, JWKS, True),
    (ISSUER, ISSUER + "/api/auth/jwks", True),
    (ISSUER, "https://evil.example.test/api/auth/jwks", False),     # https but another origin
    (ISSUER, "http://web.example.test/api/auth/jwks", False),       # http to a dotted (public) name
    (ISSUER, "http://u:p@web:3000/api/auth/jwks", False),           # credentials
    ("http://enigma.example.test", JWKS, False),                     # issuer must be https
    (ISSUER + "/path", JWKS, False),                                  # issuer must be a bare origin
])
def test_issuer_and_jwks_url_policy(issuer, jwks, ok):
    s = settings(auth_issuer=issuer, auth_jwks_url=jwks)
    assert (not [p for p in s.validate_for_startup() if "ENIGMA_AUTH_" in p]) is ok


def test_production_requires_issuer_and_jwks():
    s = Settings(env="production", cron_secret="c" * 32, auth_provider="betterauth", store="postgres", db_dsn="postgresql://u@h/d?sslmode=verify-full")
    assert any("ENIGMA_AUTH_ISSUER" in p for p in s.validate_for_startup())
    assert not settings(env="production", store="postgres", db_dsn="postgresql://u@h/d?sslmode=verify-full").validate_for_startup()


@pytest.mark.parametrize("alg,key", [("HS256", "s" * 32), ("RS256", _RSA)])
def test_other_algorithms_are_refused(alg, key):
    with pytest.raises(ApiError):
        verifier().verify(mint(alg=alg, key=key))


def test_betterauth_tokens_are_refused_when_provider_is_neon():
    v = UserVerifier(Settings(env="test", auth_provider="neon", neon_auth_base_url="https://n.example.test/a", auth_issuer=ISSUER, auth_jwks_url=JWKS),
                     key_resolver=lambda _t: _KEY.public_key())
    with pytest.raises(ApiError):
        v.verify(mint())


def test_jwks_fetch_rotation_and_fail_closed():
    docs = {"n": 0, "keys": [jwk_of(_KEY, KID)]}
    t = [1000.0]

    def fetch(url):
        assert url == JWKS
        docs["n"] += 1
        return {"keys": docs["keys"]}
    v = UserVerifier(settings(), jwks_fetch=fetch, clock=lambda: t[0])
    assert v.verify(mint()).user_id == f"ba:{UID}"
    docs["keys"] = [jwk_of(_OTHER, "ba-kid-2")]                         # rotated
    t[0] += 301
    assert v.verify(mint(key=_OTHER, kid="ba-kid-2")).user_id == f"ba:{UID}"
    with pytest.raises(ApiError):                                         # the retired key is gone after the refresh
        v.verify(mint(kid=KID))


def test_unreachable_jwks_fails_closed():
    def fetch(_url):
        raise OSError("down")
    with pytest.raises(ApiError):
        UserVerifier(settings(), jwks_fetch=fetch).verify(mint())


def test_default_fetch_allows_private_http_only_when_enabled():
    with pytest.raises(OSError):
        JwksCache("http://web:3000/x")._default_fetch("http://web:3000/x")
    with pytest.raises(OSError):
        JwksCache("http://web.example.test/x", allow_private_http=True)._default_fetch("http://web.example.test/x")


def test_two_users_are_distinct_identities():
    a = verifier().verify(mint(UID)).user_id
    b = verifier().verify(mint("3f0f3f0e-0000-4000-8000-000000000002")).user_id
    assert a != b


def test_loopback_http_issuer_only_in_dev_or_test():
    assert settings(env="test", auth_issuer="http://127.0.0.1:3000", auth_jwks_url="http://127.0.0.1:3000/api/auth/jwks").auth_origin == "http://127.0.0.1:3000"
    assert settings(env="production", auth_issuer="http://127.0.0.1:3000").auth_origin == ""
    assert settings(env="test", auth_issuer="http://enigma.example.test").auth_origin == ""
