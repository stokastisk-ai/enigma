"""Cognito JWT verification (unit/integration with LOCALLY generated RSA keys).

This proves claim handling and tenant isolation in our code. It is NOT evidence about a real Cognito user pool
(JWKS fetch, token issuance, sign-up flows) -- that is covered by the live Railway E2E (a real pool, enigma-prod, exists since 2026-11-24).
"""
from __future__ import annotations

import json
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
    problems = Settings(env="production", store="dynamodb", ddb_table="enigma-prod", cron_secret="c" * 32, auth_provider="cognito").validate_for_startup()
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


def test_cognito_ids_are_always_namespaced():
    # Cognito identities are always "cognito:"-prefixed, so memberships keyed on user_id can never be shared with a neon:/dev identity.
    assert verifier().verify(mint("user_123")).user_id != "user_123"


# ---- helpers for signed test tokens
def _jwks(pub, kid):
    import json
    from jwt.algorithms import RSAAlgorithm
    d = json.loads(RSAAlgorithm.to_jwk(pub)); d.update(kid=kid, alg="RS256", use="sig")
    return json.dumps({"keys": [d]})


def _no_network(url):
    raise OSError("network unreachable (VPC without NAT)")


def _signed(key, kid, **kw):
    claims = {"sub": "u-pin", "iss": ISS, "iat": int(time.time()), "exp": int(time.time()) + 600, "token_use": "access", "client_id": CLIENT, **kw}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})











# ---- rotation: cached JWKS, throttled refresh on unknown kid, retired keys dropped, fail closed
class _Clk:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def _doc(*pairs):
    import json
    from jwt.algorithms import RSAAlgorithm
    keys = []
    for pub, kid in pairs:
        d = json.loads(RSAAlgorithm.to_jwk(pub)); d.update(kid=kid, alg="RS256", use="sig"); keys.append(d)
    return {"keys": keys}


class _Idp:
    """Fake Cognito JWKS endpoint that counts requests and can be switched to 'down'."""
    def __init__(self, doc): self.doc, self.hits, self.down = doc, 0, False
    def __call__(self, url):
        assert url == f"{ISS}/.well-known/jwks.json"
        self.hits += 1
        if self.down: raise OSError("down")
        return self.doc


def test_normal_mode_fetches_once_then_serves_from_cache():
    idp, clk = _Idp(_doc((_KEY.public_key(), "k1"))), _Clk()
    v = UserVerifier(settings(), jwks_fetch=idp, clock=clk)
    for _ in range(5):
        assert v.verify(_signed(_KEY, "k1")).user_id == "cognito:u-pin"
    assert idp.hits == 1


def test_key_rotation_is_picked_up_without_a_redeploy():
    idp, clk = _Idp(_doc((_KEY.public_key(), "k1"))), _Clk()
    v = UserVerifier(settings(), jwks_fetch=idp, clock=clk)
    assert v.verify(_signed(_KEY, "k1"))
    idp.doc = _doc((_KEY.public_key(), "k1"), (_OTHER.public_key(), "k2"))                     # Cognito publishes a second key
    clk.t += 301                                                                               # past the refresh throttle
    assert v.verify(_signed(_OTHER, "k2")).user_id == "cognito:u-pin"
    assert v.verify(_signed(_KEY, "k1"))                                                       # old key still valid while published


def test_forged_kid_flood_cannot_hammer_the_idp():
    idp, clk = _Idp(_doc((_KEY.public_key(), "k1"))), _Clk()
    v = UserVerifier(settings(), jwks_fetch=idp, clock=clk)
    v.verify(_signed(_KEY, "k1"))
    base = idp.hits
    for i in range(200):
        with pytest.raises(ApiError):
            v.verify(_signed(_OTHER, f"forged-{i}"))
    assert idp.hits - base <= 1                                                                # at most one refresh inside the throttle window
    clk.t += 301
    with pytest.raises(ApiError):
        v.verify(_signed(_OTHER, "forged-x"))
    assert idp.hits - base <= 2


def test_retired_key_is_dropped_after_a_successful_refresh():
    idp, clk = _Idp(_doc((_KEY.public_key(), "k1"), (_OTHER.public_key(), "k2"))), _Clk()
    v = UserVerifier(settings(), jwks_fetch=idp, clock=clk)
    assert v.verify(_signed(_KEY, "k1"))
    idp.doc = _doc((_OTHER.public_key(), "k2"))                                                # Cognito retires k1
    clk.t += 3601                                                                              # cache TTL elapsed -> refresh on next use
    assert v.verify(_signed(_OTHER, "k2"))
    with pytest.raises(ApiError) as e:
        v.verify(_signed(_KEY, "k1"))                                                          # a token signed by the retired key no longer verifies
    assert e.value.status == 401


def test_idp_outage_keeps_known_keys_and_fails_closed_for_unknown():
    idp, clk = _Idp(_doc((_KEY.public_key(), "k1"))), _Clk()
    v = UserVerifier(settings(), jwks_fetch=idp, clock=clk)
    assert v.verify(_signed(_KEY, "k1"))
    idp.down = True
    clk.t += 4000
    assert v.verify(_signed(_KEY, "k1"))                                                       # existing key still works during an outage
    with pytest.raises(ApiError):
        v.verify(_signed(_OTHER, "never-seen"))                                                 # unknown stays refused





def test_a_garbage_jwks_response_cannot_poison_the_cache():
    idp, clk = _Idp(_doc((_KEY.public_key(), "k1"))), _Clk()
    v = UserVerifier(settings(), jwks_fetch=idp, clock=clk)
    assert v.verify(_signed(_KEY, "k1"))
    idp.doc = {"keys": []}                                                                      # empty / hostile document
    clk.t += 4000
    assert v.verify(_signed(_KEY, "k1"))                                                        # previous good keys retained
    idp.doc = {"nope": 1}
    clk.t += 4000
    assert v.verify(_signed(_KEY, "k1"))


def test_malformed_region_or_pool_id_is_refused_at_startup():
    for region, pool in (("us-east-1/../x", "us-east-1_POOL"), ("us-east-1", "evil.example/x"), ("EU", "us-east-1_POOL")):
        assert any("malformed" in p for p in settings(cognito_region=region, cognito_user_pool_id=pool).validate_for_startup())
    assert not any("malformed" in p for p in settings().validate_for_startup())


def test_default_fetcher_refuses_non_https_urls():
    with pytest.raises(OSError):
        from enigma_api.security import JwksCache
        JwksCache("http://cognito-idp.us-east-1.amazonaws.com/x/.well-known/jwks.json")._default_fetch("http://cognito-idp.us-east-1.amazonaws.com/x/.well-known/jwks.json")


# ---- JWT header / claim attacks (the verifier must trust ONLY its own cached/pinned keys and the pool issuer)
def _attack_token(key, headers=None, **claims):
    base = {"sub": "u-atk", "iss": ISS, "iat": int(time.time()), "exp": int(time.time()) + 600, "token_use": "access", "client_id": CLIENT}
    return jwt.encode({**base, **claims}, key, algorithm="RS256", headers={"kid": "k1", **(headers or {})})


def _good_verifier():
    idp = _Idp(_doc((_KEY.public_key(), "k1")))
    return UserVerifier(settings(), jwks_fetch=idp, clock=_Clk()), idp


def test_embedded_jwk_header_signed_by_the_attacker_is_ignored():
    from jwt.algorithms import RSAAlgorithm
    evil = json.loads(RSAAlgorithm.to_jwk(_OTHER.public_key())); evil["kid"] = "k1"
    v, _ = _good_verifier()
    with pytest.raises(ApiError):
        v.verify(_attack_token(_OTHER, headers={"jwk": evil}))                  # attacker signs with their key and ships the public half in the token


@pytest.mark.parametrize("hdr", [{"jku": "https://evil.example/jwks.json"}, {"x5u": "https://evil.example/cert.pem"}, {"x5c": ["MIIB"]}, {"crit": ["exp"]}])
def test_key_location_headers_are_never_followed(hdr):
    v, idp = _good_verifier()
    with pytest.raises(ApiError):
        v.verify(_attack_token(_OTHER, headers=hdr))
    assert idp.hits <= 1                                                        # no request to the attacker's URL; only our own pool JWKS may be consulted


@pytest.mark.parametrize("kid", ["../../etc/passwd", "k1\u0000", "k1 ", "K1", "", "k1" * 500, "' OR '1'='1"])
def test_hostile_or_near_miss_kids_are_refused(kid):
    v, _ = _good_verifier()
    with pytest.raises(ApiError):
        v.verify(_attack_token(_KEY, headers={"kid": kid}))


def test_not_before_in_the_future_and_missing_exp_are_refused():
    v, _ = _good_verifier()
    with pytest.raises(ApiError):
        v.verify(_attack_token(_KEY, nbf=int(time.time()) + 3600))
    tok = jwt.encode({"sub": "u", "iss": ISS, "iat": int(time.time()), "token_use": "access", "client_id": CLIENT}, _KEY, algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(ApiError):
        v.verify(tok)                                                           # no exp => never valid forever


@pytest.mark.parametrize("claims", [{"sub": ""}, {"iss": ISS + "/"}, {"iss": ISS.upper()}, {"iss": ISS.replace("us-east-1", "eu-west-1")}, {"client_id": CLIENT.upper()}, {"client_id": [CLIENT]}])
def test_near_miss_claims_are_refused(claims):
    v, _ = _good_verifier()
    with pytest.raises(ApiError):
        v.verify(_attack_token(_KEY, **claims))


def test_subject_is_namespaced_so_it_cannot_impersonate_another_provider_or_user():
    v, _ = _good_verifier()
    ident = v.verify(_attack_token(_KEY, sub="user_victim"))
    assert ident.user_id == "cognito:user_victim" and ident.auth == "cognito"      # a Cognito sub can never equal a Clerk/dev user id
    for bad in ("cognito:x", "a:b", "x" * 129, "a b", "../x", "u\u0000", "é", 42):
        with pytest.raises(ApiError):                                                # odd subjects (incl. a forged `cognito:` prefix) are refused outright
            v.verify(_attack_token(_KEY, sub=bad))


def test_a_token_for_a_different_user_pool_with_a_valid_signature_shape_is_refused():
    v, _ = _good_verifier()
    other_pool = f"https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OTHERPOOL"
    with pytest.raises(ApiError):
        v.verify(_attack_token(_KEY, iss=other_pool))                            # even if the signature were somehow valid, another pool's tokens are refused


def test_the_api_never_returns_verifier_internals_in_the_401():
    with TestClient(create_app(settings(), store=MemoryStore(Clock()), broadcaster=RecordingBroadcaster(), clock=Clock(), verifier=_good_verifier()[0]), raise_server_exceptions=False) as c:
        r = c.get("/api/v1/app/me", headers={"Authorization": f"Bearer {_attack_token(_OTHER)}"})
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        txt = r.text.lower()
        assert not any(w in txt for w in ("signature", "kid", "jwks", "issuer", "expired", "pyjwt", "cognito"))        # no oracle for an attacker


def test_only_the_configured_provider_is_accepted():
    """Production runs ONE provider. With cognito configured, an EdDSA (Neon-shaped) token is refused; with neon configured, an RS256 token is refused."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    ed = jwt.encode({"sub": "x", "iat": int(time.time()), "exp": int(time.time()) + 60}, Ed25519PrivateKey.generate(), algorithm="EdDSA", headers={"kid": "k"})
    with pytest.raises(ApiError):
        verifier().verify(ed)
    neon = Settings(auth_provider="neon", cognito_region="us-east-1", cognito_user_pool_id=POOL, cognito_app_client_id=CLIENT)
    with pytest.raises(ApiError):
        UserVerifier(neon, key_resolver=lambda t: _KEY.public_key()).verify(mint("alice"))
