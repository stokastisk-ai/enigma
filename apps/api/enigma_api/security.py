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
    user_id: str          # Clerk `sub`
    auth: str             # "clerk" | "dev"


KeyResolver = Callable[[str], Any]   # token -> signing key (injectable for tests)


class UserVerifier:
    def __init__(self, settings: Settings, key_resolver: KeyResolver | None = None):
        self.s = settings
        self._resolver = key_resolver
        self._jwk_client: jwt.PyJWKClient | None = None

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
