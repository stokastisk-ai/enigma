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
    store: str = "memory"                          # memory | supabase
    # Supabase (server-only; the service-role key must never reach a browser bundle)
    supabase_url: str = Field(default="", validation_alias="SUPABASE_URL")
    supabase_service_role_key: str = Field(default="", validation_alias="SUPABASE_SERVICE_ROLE_KEY")
    # Clerk
    clerk_jwks_url: str = Field(default="", validation_alias="CLERK_JWKS_URL")
    clerk_issuer: str = Field(default="", validation_alias="CLERK_ISSUER")
    clerk_authorized_parties: str = Field(default="", validation_alias="CLERK_AUTHORIZED_PARTIES")  # comma-separated origins (azp)
    # dev-only auth (HS256 tokens minted by the local BFF/tests). Refused in production and on Vercel.
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
    def is_production(self) -> bool:
        return self.env == "production" or bool(os.environ.get("VERCEL_ENV") == "production")

    @property
    def on_vercel(self) -> bool:
        return bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))

    def validate_for_startup(self) -> list[str]:
        """Return a list of fatal configuration problems. Called at app creation; production refuses to boot with any."""
        problems: list[str] = []
        prod = self.is_production or self.on_vercel
        if self.dev_auth and (prod or self.env not in ("development", "test")):
            problems.append("ENIGMA_DEV_AUTH must not be enabled in production/Vercel/non-dev environments")
        if self.dev_auth and len(self.dev_jwt_secret) < 32:
            problems.append("ENIGMA_DEV_JWT_SECRET must be at least 32 characters when dev auth is on")
        if prod:
            if self.store != "supabase":
                problems.append("ENIGMA_STORE must be 'supabase' in production (the memory store is not durable)")
            if not (self.supabase_url.startswith("https://") and self.supabase_service_role_key):
                problems.append("SUPABASE_URL (https) and SUPABASE_SERVICE_ROLE_KEY are required in production")
            if not (self.clerk_jwks_url.startswith("https://") and self.clerk_issuer.startswith("https://")):
                problems.append("CLERK_JWKS_URL and CLERK_ISSUER (https) are required in production")
            if len(self.cron_secret) < 24:
                problems.append("CRON_SECRET (>=24 chars) is required in production")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
