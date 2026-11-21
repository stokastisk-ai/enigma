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
    store: str = "memory"                          # memory | supabase | postgres
    # Supabase (server-only; the service-role key must never reach a browser bundle)
    supabase_url: str = Field(default="", validation_alias="SUPABASE_URL")
    supabase_service_role_key: str = Field(default="", validation_alias="SUPABASE_SERVICE_ROLE_KEY")
    # Clerk
    clerk_jwks_url: str = Field(default="", validation_alias="CLERK_JWKS_URL")
    clerk_issuer: str = Field(default="", validation_alias="CLERK_ISSUER")
    clerk_authorized_parties: str = Field(default="", validation_alias="CLERK_AUTHORIZED_PARTIES")  # comma-separated origins (azp)
    # PostgreSQL (AWS RDS) store: libpq DSN. Server-only secret; with sslmode=verify-full for RDS. Never logged.
    db_dsn: str = Field(default="", validation_alias="ENIGMA_DB_DSN")
    # Identity provider for the user plane: "clerk" (historical default) | "cognito" (AWS). Flag-selected; Clerk code is retained until
    # the AWS path is verified live (skills.md section 14).
    auth_provider: str = "clerk"
    cognito_region: str = Field(default="", validation_alias="COGNITO_REGION")
    cognito_user_pool_id: str = Field(default="", validation_alias="COGNITO_USER_POOL_ID")
    cognito_app_client_id: str = Field(default="", validation_alias="COGNITO_APP_CLIENT_ID")
    # Optional PINNED signing keys (the pool's JWKS document, JSON). Lets a VPC-attached Lambda verify tokens with NO outbound network
    # (no NAT gateway). Operational risk: if Cognito rotates the pool's keys, tokens with the new `kid` are refused until this is updated.
    cognito_jwks_json: str = Field(default="", validation_alias="COGNITO_JWKS_JSON")
    cognito_token_use: str = Field(default="access", validation_alias="COGNITO_TOKEN_USE")   # access | id
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
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}" if self.cognito_region and self.cognito_user_pool_id else ""

    @property
    def cognito_jwks_url(self) -> str:
        return self.cognito_issuer + "/.well-known/jwks.json" if self.cognito_issuer else ""

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
        if self.auth_provider not in ("clerk", "cognito"):
            problems.append("ENIGMA_AUTH_PROVIDER must be 'clerk' or 'cognito'")
        if self.cognito_jwks_json:
            try:
                import json as _json
                keys = _json.loads(self.cognito_jwks_json).get("keys")
                if not (isinstance(keys, list) and keys and all(isinstance(k, dict) and k.get("kid") and k.get("kty") == "RSA" for k in keys)):
                    raise ValueError
            except ValueError:
                problems.append("COGNITO_JWKS_JSON must be a JWKS document with RSA keys that carry a kid")
        if self.cognito_token_use not in ("access", "id"):
            problems.append("COGNITO_TOKEN_USE must be 'access' or 'id'")
        if self.dev_auth and (prod or self.env not in ("development", "test")):
            problems.append("ENIGMA_DEV_AUTH must not be enabled in production/Vercel/non-dev environments")
        if self.dev_auth and len(self.dev_jwt_secret) < 32:
            problems.append("ENIGMA_DEV_JWT_SECRET must be at least 32 characters when dev auth is on")
        if prod:
            if self.store not in ("supabase", "postgres"):
                problems.append("ENIGMA_STORE must be 'supabase' or 'postgres' in production (the memory store is not durable)")
            if self.store == "postgres":
                if not self.db_dsn:
                    problems.append("ENIGMA_DB_DSN is required when ENIGMA_STORE=postgres")
                elif "sslmode=disable" in self.db_dsn or "sslmode=allow" in self.db_dsn or "sslmode=prefer" in self.db_dsn:
                    problems.append("ENIGMA_DB_DSN must not weaken TLS in production (use sslmode=require or verify-full)")
            elif not (self.supabase_url.startswith("https://") and self.supabase_service_role_key):
                problems.append("SUPABASE_URL (https) and SUPABASE_SERVICE_ROLE_KEY are required in production")
            if self.auth_provider == "cognito":
                if not (self.cognito_issuer and self.cognito_app_client_id):
                    problems.append("COGNITO_REGION, COGNITO_USER_POOL_ID and COGNITO_APP_CLIENT_ID are required when ENIGMA_AUTH_PROVIDER=cognito")
            elif not (self.clerk_jwks_url.startswith("https://") and self.clerk_issuer.startswith("https://")):
                problems.append("CLERK_JWKS_URL and CLERK_ISSUER (https) are required in production")
            if len(self.cron_secret) < 24:
                problems.append("CRON_SECRET (>=24 chars) is required in production")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
