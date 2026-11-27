"""Two authentication planes.

* Runtime plane: ``Authorization: Bearer enig_rt_<8-char prefix>_<secret>``. Only sha256(secret) is stored.
  A token resolves to exactly one runtime (and therefore one workspace + project); nothing in a request body can widen that.
* User plane: exactly ONE configured provider. ``cognito``: an Amazon Cognito user-pool JWT (RS256, issuer pinned to the pool, token_use and
  app client enforced). ``neon`` (rollback path only): a Neon Managed Better Auth JWT (EdDSA, issuer and audience = the Auth URL origin).
  A token of the other provider's algorithm is refused. Every other algorithm is refused.
  A dev-only HS256 mode exists for the local mock E2E; Settings.validate_for_startup() forbids it in production / on Railway.
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
    user_id: str          # namespaced subject: Cognito `cognito:<sub>`, Neon `neon:<sub>`; dev tokens use their own `sub`
    auth: str             # "betterauth" | "cognito" | "neon" | "dev"


KeyResolver = Callable[[str], Any]   # token -> signing key (injectable for tests)


_SUBJECT = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _valid_subject(sub: Any) -> str:
    """A verified token must still carry a usable subject. An empty/odd `sub` would collapse every such user into one identity (and so one tenant);
    ':' is excluded so a token can never forge the `neon:` namespace prefix. Neon subs are UUIDs, which this accepts."""
    if not isinstance(sub, str) or not _SUBJECT.fullmatch(sub):
        raise ApiError(401, "unauthorized", "authentication required", headers={"www-authenticate": "Bearer"})
    return sub


class JwksCache:
    """Neon Auth signing keys with safe rotation.

    * Normal mode: keys are fetched from the Auth URL's public JWKS endpoint (HTTPS, short timeout) on first use, then cached.
    * An unknown ``kid`` triggers ONE refresh attempt, throttled to once per ``min_refresh_s`` (a flood of forged ``kid`` values cannot hammer
      the JWKS endpoint or add latency to every request). The published key set replaces the cache, so a key Neon retired is dropped
      at the next successful refresh (a known ``kid`` also triggers a refresh once the cache is older than ``ttl_s``).
    * A failed refresh never widens trust: existing keys are kept, unknown kids still fail.
    Not a secret: JWKS documents contain public keys only.
    """

    def __init__(self, url: str, fetch: Callable[[str], dict] | None = None, clock: Callable[[], float] | None = None,
                 min_refresh_s: float = 300.0, ttl_s: float = 3600.0, allow_private_http: bool = False):
        import time as _t
        self.allow_private_http = allow_private_http
        self.url, self._fetch, self._clock = url, fetch or self._default_fetch, clock or _t.monotonic
        self.min_refresh_s, self.ttl_s = min_refresh_s, ttl_s
        self._keys: dict[str, Any] = {}
        self._last_attempt: float | None = None
        self._last_success: float | None = None
        self.fetches = 0                                                  # observable for tests / diagnostics

    def _default_fetch(self, url: str) -> dict:
        import json
        import urllib.request
        from urllib.parse import urlparse
        u = urlparse(url)
        private_http = self.allow_private_http and u.scheme == "http" and bool(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", u.hostname or ""))
        if u.scheme != "https" and not private_http:
            raise OSError("JWKS URL must be https (or http to a private single-label container host when explicitly allowed)")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never route key fetches through an ambient proxy
        with opener.open(url, timeout=2.0) as r:                          # noqa: S310  (scheme/host validated above; URL from validated settings)
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
        self._jwks_fetch, self._clock = jwks_fetch, clock
        self._neon_keys: JwksCache | None = None
        self._ba_keys: JwksCache | None = None
        self._cognito_keys: JwksCache | None = None

    def _verify_cognito(self, token: str) -> "UserIdentity":
        """Cognito user-pool JWT: RS256 only, issuer pinned to the configured pool, exp/iat/sub required, ``token_use`` must be the configured one, and
        the app client must be ours (access tokens carry ``client_id``, id tokens carry ``aud``). A token for another pool or client is refused.
        Cognito marks an account usable only after e-mail verification (auto-verified attribute = email), so no separate claim is trusted here."""
        unauthorized = ApiError(401, "unauthorized", "authentication required", headers={"www-authenticate": "Bearer"})
        s = self.s
        if not (s.cognito_issuer and s.cognito_app_client_id):
            raise unauthorized
        if self._resolver:
            key = self._resolver(token)
        else:
            if self._cognito_keys is None:
                self._cognito_keys = JwksCache(s.cognito_jwks_url, self._jwks_fetch, self._clock)
            key = self._cognito_keys.key_for(jwt.get_unverified_header(token).get("kid"))
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=s.cognito_issuer,
                            options={"require": ["exp", "iat", "sub", "iss"], "verify_aud": False})
        if claims.get("token_use") != s.cognito_token_use:
            raise unauthorized
        client = claims.get("aud") if s.cognito_token_use == "id" else claims.get("client_id")
        if client != s.cognito_app_client_id:
            raise unauthorized
        return UserIdentity(f"cognito:{_valid_subject(claims['sub'])}", "cognito")

    def _verify_neon(self, token: str) -> "UserIdentity":
        """Neon Managed Better Auth JWT: EdDSA (Ed25519), 15-minute expiry. `iss` AND `aud` must equal the origin of the configured Auth URL, so a token from
        any other Neon project/branch is refused. A banned account, or (by default) an account whose e-mail is not verified, is refused. The `sub` is
        namespaced as `neon:<sub>` so it cannot collide with a dev identity (no e-mail based linking)."""
        unauthorized = ApiError(401, "unauthorized", "authentication required", headers={"www-authenticate": "Bearer"})
        s = self.s
        origin = s.neon_origin
        if not origin:
            raise unauthorized
        if self._resolver:
            key = self._resolver(token)
        else:
            if self._neon_keys is None:
                self._neon_keys = JwksCache(s.neon_jwks_url, self._jwks_fetch, self._clock)
            key = self._neon_keys.key_for(jwt.get_unverified_header(token).get("kid"))
        claims = jwt.decode(token, key, algorithms=["EdDSA"], issuer=origin, audience=origin,
                            options={"require": ["exp", "iat", "sub", "iss", "aud"]})
        if claims.get("banned") is True:
            raise unauthorized
        if s.neon_require_verified_email and claims.get("emailVerified") is not True:
            raise unauthorized
        return UserIdentity(f"neon:{_valid_subject(claims['sub'])}", "neon")

    def _verify_betterauth(self, token: str) -> "UserIdentity":
        """Self-hosted Better Auth JWT (jwt plugin): EdDSA (Ed25519), 15-minute expiry, `iss` AND `aud` equal to ENIGMA_AUTH_ISSUER's origin. Same
        rules as the Neon verifier (banned refused, verified e-mail required by default). The `sub` is the Better Auth user UUID, namespaced `ba:<sub>`;
        users migrated from Neon keep their UUID and are re-keyed `neon:<uuid>` -> `ba:<uuid>` by scripts/oci_import.py (no e-mail based linking)."""
        unauthorized = ApiError(401, "unauthorized", "authentication required", headers={"www-authenticate": "Bearer"})
        s = self.s
        origin = s.auth_origin
        if not origin or not s.auth_jwks_ok:
            raise unauthorized
        if self._resolver:
            key = self._resolver(token)
        else:
            if self._ba_keys is None:
                self._ba_keys = JwksCache(s.auth_jwks_url, self._jwks_fetch, self._clock, allow_private_http=True)
            key = self._ba_keys.key_for(jwt.get_unverified_header(token).get("kid"))
        claims = jwt.decode(token, key, algorithms=["EdDSA"], issuer=origin, audience=origin,
                            options={"require": ["exp", "iat", "sub", "iss", "aud"]})
        if claims.get("banned") is True:
            raise unauthorized
        if s.auth_require_verified_email and claims.get("emailVerified") is not True:
            raise unauthorized
        return UserIdentity(f"ba:{_valid_subject(claims['sub'])}", "betterauth")

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
            if alg == "RS256" and self.s.auth_provider == "cognito":
                return self._verify_cognito(token)
            if alg == "EdDSA" and self.s.auth_provider == "neon":
                return self._verify_neon(token)
            if alg == "EdDSA" and self.s.auth_provider == "betterauth":
                return self._verify_betterauth(token)
            raise unauthorized
        except ApiError:
            raise
        except (jwt.PyJWTError, KeyError, OSError, TypeError, ValueError):   # TypeError/ValueError: key-type mismatch or malformed JWK must be a 401, never a 500
            raise unauthorized from None
