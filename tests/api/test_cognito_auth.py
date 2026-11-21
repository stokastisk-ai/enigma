"""Cognito JWT verification (unit/integration with LOCALLY generated RSA keys).

This proves claim handling and tenant isolation in our code. It is NOT evidence about a real Cognito user pool
(JWKS fetch, token issuance, sign-up flows) -- that stays BLOCKED until a pool exists (docs/AWS_MIGRATION_REPORT.md).
"""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from api_helpers import Clock
from enigma_api.errors import ApiError
from enigma_api.main import create_app
from enigma_api.realtime import RecordingBroadcaster
from enigma_api.security import UserVerifier
from enigma_api.settings import Settings
from enigma_api.store.memory import MemoryStore

REGION, POOL, CLIENT = "us-east-1", "us-east-1_TESTPOOL", "testclientid123"
ISS = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def settings(**kw) -> Settings:
    base = dict(env="test", auth_provider="cognito", cognito_region=REGION, cognito_user_pool_id=POOL, cognito_app_client_id=CLIENT, cron_secret="c" * 32)
    return Settings(**{**base, **kw})


def mint(sub="u-1", *, key=_KEY, iss=ISS, use="access", client=CLIENT, exp_in=600, alg="RS256", drop=()):
    claims = {"sub": sub, "iss": iss, "iat": int(time.time()), "exp": int(time.time()) + exp_in, "token_use": use}
    claims["client_id" if use == "access" else "aud"] = client
    for d in drop:
        claims.pop(d, None)
    return jwt.encode(claims, key, algorithm=alg)


def verifier(**kw) -> UserVerifier:
    return UserVerifier(settings(**kw), key_resolver=lambda _t: _KEY.public_key())


def test_valid_access_token_namespaces_the_subject():
    ident = verifier().verify(mint("abc"))
    assert ident.user_id == "cognito:abc" and ident.auth == "cognito"


@pytest.mark.parametrize("tok", [
    mint(iss="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OTHER"),     # wrong pool
    mint(client="someone-elses-client"),                                         # wrong app client
    mint(use="id"),                                                              # id token where access expected
    mint(exp_in=-5),                                                             # expired
    mint(key=_OTHER),                                                            # signed by an unknown key
    mint(drop=("token_use",)),
    mint(drop=("iss",)),
    mint(drop=("sub",)),
])
def test_bad_tokens_are_rejected(tok):
    with pytest.raises(ApiError) as e:
        verifier().verify(tok)
    assert e.value.status == 401


def test_id_token_mode_requires_aud_binding():
    v = verifier(cognito_token_use="id")
    assert v.verify(mint("x", use="id")).user_id == "cognito:x"
    with pytest.raises(ApiError):
        v.verify(mint("x", use="id", client="other"))
    with pytest.raises(ApiError):
        v.verify(mint("x", use="access"))


def test_hs256_is_refused_without_dev_auth_even_if_secret_is_known():
    forged = jwt.encode({"sub": "u", "aud": "enigma-dev", "exp": int(time.time()) + 60}, "s" * 40, algorithm="HS256")
    with pytest.raises(ApiError):
        verifier().verify(forged)


def test_alg_none_is_refused():
    tok = jwt.encode({"sub": "u", "iss": ISS, "exp": int(time.time()) + 60}, None, algorithm="none")
    with pytest.raises(ApiError):
        verifier().verify(tok)


def test_missing_cognito_config_fails_closed():
    v = UserVerifier(settings(cognito_user_pool_id=""), key_resolver=lambda _t: _KEY.public_key())
    with pytest.raises(ApiError):
        v.verify(mint())


def test_production_requires_cognito_settings():
    problems = Settings(env="production", store="supabase", supabase_url="https://x.example", supabase_service_role_key="k",
                        cron_secret="c" * 32, auth_provider="cognito").validate_for_startup()
    assert any("COGNITO_" in p for p in problems)
    assert Settings(auth_provider="bogus").validate_for_startup()


def test_two_cognito_tenants_cannot_see_each_other():
    clock = Clock()
    app = create_app(settings(), store=MemoryStore(clock), broadcaster=RecordingBroadcaster(), clock=clock,
                     verifier=verifier())
    with TestClient(app, raise_server_exceptions=False) as c:
        a, b = {"Authorization": f"Bearer {mint('alice')}"}, {"Authorization": f"Bearer {mint('bob')}"}
        ra = c.post("/api/v1/app/workspaces", json={"name": "A"}, headers=a)
        assert ra.status_code == 201, ra.text
        ws_a = ra.json()["workspace"]["id"]
        proj_a = ra.json()["project"]["id"]
        assert c.post("/api/v1/app/workspaces", json={"name": "B"}, headers=b).status_code == 201
        # Bob must get 404 (not 403, not data) on Alice's workspace-scoped resources
        assert c.get(f"/api/v1/app/projects/{proj_a}/runtimes", headers=b).status_code == 404
        assert c.get(f"/api/v1/app/projects/{proj_a}/runtimes", headers=a).status_code == 200
        names = [w["id"] for w in c.get("/api/v1/app/workspaces", headers=b).json()["items"]]
        assert ws_a not in names
        assert c.get("/api/v1/app/workspaces").status_code == 401


def test_cognito_and_clerk_ids_never_collide():
    # A Clerk user whose id happens to look like a Cognito one is still distinct: Clerk ids are never "cognito:"-prefixed by us,
    # and Cognito identities always are, so memberships keyed on user_id cannot be shared across providers.
    assert verifier().verify(mint("user_123")).user_id != "user_123"


# ---- pinned JWKS (NAT-free VPC Lambda): no network, fail closed on unknown kid
def _jwks(pub, kid):
    import json
    from jwt.algorithms import RSAAlgorithm
    d = json.loads(RSAAlgorithm.to_jwk(pub)); d.update(kid=kid, alg="RS256", use="sig")
    return json.dumps({"keys": [d]})


def _signed(key, kid, **kw):
    claims = {"sub": "u-pin", "iss": ISS, "iat": int(time.time()), "exp": int(time.time()) + 600, "token_use": "access", "client_id": CLIENT, **kw}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def test_pinned_jwks_verifies_without_any_network(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    v = UserVerifier(settings(cognito_jwks_json=_jwks(_KEY.public_key(), "pinned-1")))
    assert v.verify(_signed(_KEY, "pinned-1")).user_id == "cognito:u-pin"


def test_pinned_jwks_unknown_kid_wrong_key_and_forgery_fail_closed():
    v = UserVerifier(settings(cognito_jwks_json=_jwks(_KEY.public_key(), "pinned-1")))
    for tok in (_signed(_KEY, "rotated-2"), _signed(_OTHER, "pinned-1"), _signed(_KEY, "pinned-1", client_id="other")):
        with pytest.raises(ApiError) as e:
            v.verify(tok)
        assert e.value.status == 401


def test_bad_pinned_jwks_is_refused_at_startup():
    for bad in ("not json", "{}", '{"keys": []}', '{"keys":[{"kty":"oct","kid":"x"}]}'):
        assert any("COGNITO_JWKS_JSON" in p for p in settings(cognito_jwks_json=bad).validate_for_startup())
    assert not any("COGNITO_JWKS_JSON" in p for p in settings(cognito_jwks_json=_jwks(_KEY.public_key(), "k")).validate_for_startup())
