from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Heartbeat freshness thresholds (seconds). Overridable only so end-to-end tests can reach "offline" quickly; production keeps the defaults.
RUNTIME_ONLINE_S = int(os.environ.get("ENIGMA_RUNTIME_ONLINE_S", "30"))     # younger than this => online
RUNTIME_STALE_S = int(os.environ.get("ENIGMA_RUNTIME_STALE_S", "120"))      # younger than this => stale, older => offline


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ENIGMA_", extra="ignore", env_file=None)

    env: str = "development"                      # development | test | production
    store: str = "memory"                          # memory | postgres
    # Admin allow-list for the /me is_admin flag: comma-separated verified user ids (e.g. neon:<uuid>). Empty = nobody is admin.
    admin_subjects: str = ""                         # ENIGMA_ADMIN_SUBJECTS
    build_sha: str = ""                              # ENIGMA_BUILD_SHA
    environment: str = "dev"                         # ENIGMA_ENVIRONMENT
    # PostgreSQL (Neon): libpq DSN. Server-only secret; sslmode=verify-full. Never logged.
    db_dsn: str = ""                                 # env ENIGMA_DB_DSN (prefix-derived)
    # User plane is Neon Managed Better Auth only. "neon" is the sole production value; tokens are EdDSA verified against the Auth URL's JWKS.
    auth_provider: str = "neon"
    # Neon Managed Better Auth (the only user-token provider).
    # NEON_AUTH_BASE_URL is the branch's public Auth URL (not a secret). Empty = Neon tokens are refused.
    neon_auth_base_url: str = Field(default="", validation_alias="NEON_AUTH_BASE_URL")
    neon_auth_jwks_url: str = Field(default="", validation_alias="NEON_AUTH_JWKS_URL")          # optional override; default <base>/.well-known/jwks.json
    neon_require_verified_email: bool = True                                                        # env ENIGMA_NEON_REQUIRE_VERIFIED_EMAIL
    # dev-only auth (HS256 tokens minted by the local BFF/tests). Refused in production and on Railway.
    dev_auth: bool = False
    dev_jwt_secret: str = ""
    cron_secret: str = Field(default="", validation_alias="CRON_SECRET")
    # limits
    max_body_bytes_runtime: int = 256 * 1024
    max_body_bytes_telemetry: int = 1024 * 1024
    max_body_bytes_app: int = 128 * 1024
    max_prompt_chars: int = 16000
    credential_default_days: int = 30

    @property
    def neon_origin(self) -> str:
        """Managed Better Auth sets `iss` and `aud` to the ORIGIN of the Auth URL (not the full URL). Empty when not configured or not https."""
        from urllib.parse import urlparse
        u = urlparse(self.neon_auth_base_url)
        return f"https://{u.netloc}" if u.scheme == "https" and u.netloc else ""

    @property
    def neon_jwks_url(self) -> str:
        return self.neon_auth_jwks_url or (self.neon_auth_base_url.rstrip("/") + "/.well-known/jwks.json" if self.neon_origin else "")

    @property
    def is_production(self) -> bool:
        return self.env == "production" or self.on_railway

    @property
    def on_railway(self) -> bool:
        """Railway injects these into every deployment. Treating the platform itself as production fails closed even if ENIGMA_ENV was forgotten."""
        return any(os.environ.get(k) for k in ("RAILWAY_ENVIRONMENT_ID", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"))

    def validate_for_startup(self) -> list[str]:
        """Return a list of fatal configuration problems. Called at app creation; production refuses to boot with any."""
        problems: list[str] = []
        prod = self.is_production
        if self.auth_provider != "neon":
            problems.append("ENIGMA_AUTH_PROVIDER must be 'neon' (the only supported user-auth provider)")
        if self.neon_auth_base_url or self.neon_auth_jwks_url:
            if not self.neon_origin:
                problems.append("NEON_AUTH_BASE_URL must be an https URL (it pins the token issuer and audience)")
            elif self.neon_auth_jwks_url and not self.neon_auth_jwks_url.startswith(self.neon_origin + "/"):
                problems.append("NEON_AUTH_JWKS_URL must be on the same origin as NEON_AUTH_BASE_URL")
        if self.dev_auth and (prod or self.env not in ("development", "test")):
            problems.append("ENIGMA_DEV_AUTH must not be enabled in production/Railway/non-dev environments")
        if self.dev_auth and len(self.dev_jwt_secret) < 32:
            problems.append("ENIGMA_DEV_JWT_SECRET must be at least 32 characters when dev auth is on")
        if prod:
            if self.store != "postgres":
                problems.append("ENIGMA_STORE must be 'postgres' in production (the memory store is not durable)")
            elif not self.db_dsn:
                problems.append("ENIGMA_DB_DSN is required when ENIGMA_STORE=postgres")
            elif any(f"sslmode={m}" in self.db_dsn for m in ("disable", "allow", "prefer", "require")):
                problems.append("ENIGMA_DB_DSN must use sslmode=verify-full in production (certificate and host name are verified)")
            if not self.neon_origin:
                problems.append("NEON_AUTH_BASE_URL (https) is required in production")
            if not self.neon_require_verified_email:
                problems.append("ENIGMA_NEON_REQUIRE_VERIFIED_EMAIL must be true in production")
            if len(self.cron_secret) < 24:
                problems.append("CRON_SECRET (>=24 chars) is required in production")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
