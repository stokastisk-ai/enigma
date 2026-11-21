"""Two authentication planes.

* Runtime plane: ``Authorization: Bearer enig_rt_<8-char prefix>_<secret>``. Only sha256(secret) is stored.
  A token resolves to exactly one runtime (and therefore one workspace + project); nothing in a request body can widen that.
* User plane: a Clerk session JWT (RS256, verified against the Clerk JWKS, issuer and authorized-party checked).
  A dev-only HS256 mode exists for the local mock E2E; Settings.validate_for_startup() forbids it in production / on Vercel.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import jwt

from .errors import ApiError
from .settings import Settings

TOKEN_PREFIX = "enig_rt_"
_ALPHABET = string.ascii_lowercase + string.digits
_TOKEN_RE = re.compile(r"^enig_rt_([a-z0-9]{8})_([A-Za-z0-9_-]{43})$")
SCOPE_CONNECT = "runtime:connect"


# ---------------------------------------------------------------- runtime tokens
def generate_runtime_token() -> tuple[str, str, str]:
    """Returns (full_token, prefix, sha256_hex). The full token is shown to the user exactly once."""
    prefix = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    secret = secrets.token_urlsafe(32)          # 43 url-safe chars = 256 bits
    return f"{TOKEN_PREFIX}{prefix}_{secret}", prefix, hash_secret(secret)


def hash_secret(secret: str) -> str:
    # The secret is 256 bits of CSPRNG output, so a fast hash is appropriate (nothing to brute-force or dictionary-attack).
    return hashlib.sha256(secret.encode()).hexdigest()


def parse_runtime_token(token: str) -> tuple[str, str] | None:
    m = _TOKEN_RE.match(token or "")
    return (m.group(1), m.group(2)) if m else None


@dataclass(frozen=True)
class RuntimeIdentity:
    runtime_id: str
    workspace_id: str
    project_id: str
    credential_id: str
    is_mock: bool


def _aware(dt: Any) -> datetime:
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def verify_runtime_token(store: Any, token: str, now: datetime | None = None) -> RuntimeIdentity:
    """Constant-time verification. Every failure is the same 401 so a caller cannot probe prefixes."""
    fail = ApiError(401, "invalid_token", "invalid or expired runtime token", headers={"www-authenticate": "Bearer"})
    parsed = parse_runtime_token(token)
    if not parsed:
        raise fail
    prefix, secret = parsed
    cred = store.get_credential_by_prefix(prefix)
    expected = cred["secret_hash"] if cred else "0" * 64
    ok = hmac.compare_digest(hash_secret(secret), expected)           # always compare, even for unknown prefixes
    if not (cred and ok):
        raise fail
    if cred.get("revoked_at") or _aware(cred["expires_at"]) <= (now or datetime.now(timezone.utc)):
        raise fail
    if SCOPE_CONNECT not in (cred.get("scopes") or []):
        raise ApiError(403, "insufficient_scope", "token lacks the runtime:connect scope")
    rt = store.get_runtime(cred["runtime_id"])
    if not rt or rt.get("disabled_at"):
        raise fail
    store.touch_credential(cred["id"])
    return RuntimeIdentity(rt["id"], rt["workspace_id"], rt["project_id"], cred["id"], bool(rt["is_mock"]))


# ---------------------------------------------------------------- user tokens
@dataclass(frozen=True)
class UserIdentity:
    user_id: str          # provider-namespaced subject: Clerk `sub` as-is (historical), Cognito `cognito:<sub>`; never assume equal across providers
    auth: str             # "clerk" | "cognito" | "dev"


KeyResolver = Callable[[str], Any]   # token -> signing key (injectable for tests)


class JwksCache:
    """Cognito signing keys with safe rotation.

    * Normal mode: keys are fetched from the pool's own public JWKS URL (HTTPS, short timeout) on first use, then cached.
    * An unknown ``kid`` triggers ONE refresh attempt, throttled to once per ``min_refresh_s`` (a flood of forged ``kid`` values cannot hammer
      the JWKS endpoint or add latency to every request). Cognito's published key set replaces the cache, so a key Cognito retired is dropped
      at the next successful refresh (a known ``kid`` also triggers a refresh once the cache is older than ``ttl_s``).
    * Optional pinned seed (``COGNITO_JWKS_JSON``) lets a VPC Lambda with no outbound network verify tokens offline. If the network is reachable it
      is replaced by the fetched set; if not, the pinned keys stay and an unknown ``kid`` is refused (fail closed) until the operator updates them.
    * A failed refresh never widens trust: existing keys are kept, unknown kids still fail.
    Not a secret: JWKS documents contain public keys only.
    """

    def __init__(self, url: str, pinned_json: str = "", fetch: Callable[[str], dict] | None = None, clock: Callable[[], float] | None = None,
                 min_refresh_s: float = 300.0, ttl_s: float = 3600.0):
        import time as _t
        self.url, self._fetch, self._clock = url, fetch or self._default_fetch, clock or _t.monotonic
        self.min_refresh_s, self.ttl_s = min_refresh_s, ttl_s
        self._keys: dict[str, Any] = {}
        self._last_attempt: float | None = None
        self._last_success: float | None = None
        self.fetches = 0                                                  # observable for tests / diagnostics
        if pinned_json:
            import json
            self._load(json.loads(pinned_json))

    @staticmethod
    def _default_fetch(url: str) -> dict:
        import json
        import urllib.request
        if not url.startswith("https://"):
            raise OSError("JWKS URL must be https")
        with urllib.request.urlopen(url, timeout=2.0) as r:               # noqa: S310  (https enforced above; URL built from validated settings)
            return json.loads(r.read(262144))

    def _load(self, doc: dict) -> dict[str, Any]:
        out = {k.key_id: k.key for k in jwt.PyJWKSet.from_dict(doc).keys if k.key_id}
        if not out:
            raise ValueError("empty JWKS")
        self._keys = out
        return out

    def _refresh(self, now: float) -> None:
        self._last_attempt = now
        self.fetches += 1
        try:
            self._load(self._fetch(self.url))
            self._last_success = now
        except Exception:                                                 # noqa: BLE001  network/parse failure: keep what we have, stay fail-closed
            pass

    def key_for(self, kid: str | None) -> Any:
        now = self._clock()
        stale = self._last_success is not None and now - self._last_success >= self.ttl_s
        if (kid not in self._keys or stale) and self.url and (self._last_attempt is None or now - self._last_attempt >= self.min_refresh_s):
            self._refresh(now)
        if kid in self._keys:
            return self._keys[kid]
        raise jwt.PyJWTError("unknown signing key id")


class UserVerifier:
    def __init__(self, settings: Settings, key_resolver: KeyResolver | None = None, jwks_fetch: Callable[[str], dict] | None = None,
                 clock: Callable[[], float] | None = None):
        self.s = settings
        self._resolver = key_resolver
        self._jwk_client: jwt.PyJWKClient | None = None
        self._jwks_fetch, self._clock = jwks_fetch, clock
        self._cognito_keys: JwksCache | None = None

    def _verify_cognito(self, token: str) -> "UserIdentity":
        """Cognito user-pool JWT: RS256, issuer pinned to the pool, expiry, ``token_use`` and app-client binding all enforced.

        access tokens carry ``client_id`` (no ``aud``); id tokens carry ``aud`` (= client id). Either way the client id must be ours,
        so a token minted for another app client or pool is refused.
        """
        unauthorized = ApiError(401, "unauthorized", "authentication required", headers={"www-authenticate": "Bearer"})
        s = self.s
        if not (s.cognito_issuer and s.cognito_app_client_id):
            raise unauthorized
        if self._resolver:
            key = self._resolver(token)
        else:
            if self._cognito_keys is None:
                self._cognito_keys = JwksCache(s.cognito_jwks_url, s.cognito_jwks_json, self._jwks_fetch, self._clock)
            key = self._cognito_keys.key_for(jwt.get_unverified_header(token).get("kid"))
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=s.cognito_issuer,
                            options={"require": ["exp", "iat", "sub", "iss"], "verify_aud": False})
        if claims.get("token_use") != s.cognito_token_use:
            raise unauthorized
        client = claims.get("aud") if s.cognito_token_use == "id" else claims.get("client_id")
        if client != s.cognito_app_client_id:
            raise unauthorized
        return UserIdentity(f"cognito:{claims['sub']}", "cognito")

    def _signing_key(self, token: str) -> Any:
        if self._resolver:
            return self._resolver(token)
        if not self._jwk_client:
            self._jwk_client = jwt.PyJWKClient(self.s.clerk_jwks_url, cache_keys=True, lifespan=3600, timeout=5)
        return self._jwk_client.get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> UserIdentity:
        unauthorized = ApiError(401, "unauthorized", "authentication required", headers={"www-authenticate": "Bearer"})
        if not token:
            raise unauthorized
        try:
            alg = jwt.get_unverified_header(token).get("alg")
        except jwt.PyJWTError:
            raise unauthorized from None
        try:
            if alg == "HS256":
                # Algorithm confusion guard: HS256 is only ever accepted in explicit dev mode, with the dev secret.
                if not self.s.dev_auth:
                    raise unauthorized
                claims = jwt.decode(token, self.s.dev_jwt_secret, algorithms=["HS256"], audience="enigma-dev", options={"require": ["exp", "sub"]})
                return UserIdentity(str(claims["sub"]), "dev")
            if alg != "RS256":
                raise unauthorized
            if self.s.auth_provider == "cognito":
                return self._verify_cognito(token)
            if not self.s.clerk_issuer and not self._resolver:
                raise unauthorized
            claims = jwt.decode(token, self._signing_key(token), algorithms=["RS256"], issuer=self.s.clerk_issuer or None,
                                options={"require": ["exp", "sub", "iat"], "verify_aud": False})
            azp_allowed = [a.strip() for a in self.s.clerk_authorized_parties.split(",") if a.strip()]
            if azp_allowed and claims.get("azp") not in azp_allowed:
                raise unauthorized
            if claims.get("sts") == "pending":            # Clerk: session still has pending tasks
                raise unauthorized
            return UserIdentity(str(claims["sub"]), "clerk")
        except ApiError:
            raise
        except (jwt.PyJWTError, KeyError, OSError):
            raise unauthorized from None
