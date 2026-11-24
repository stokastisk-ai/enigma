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
    store: str = "memory"                          # memory | supabase | postgres | dynamodb
    # Supabase (server-only; the service-role key must never reach a browser bundle)
    supabase_url: str = Field(default="", validation_alias="SUPABASE_URL")
    supabase_service_role_key: str = Field(default="", validation_alias="SUPABASE_SERVICE_ROLE_KEY")
    # Clerk
    clerk_jwks_url: str = Field(default="", validation_alias="CLERK_JWKS_URL")
    clerk_issuer: str = Field(default="", validation_alias="CLERK_ISSUER")
    clerk_authorized_parties: str = Field(default="", validation_alias="CLERK_AUTHORIZED_PARTIES")  # comma-separated origins (azp)
    # AWS operations page (admin-only, read-only). Names only; no credentials here.
    admin_subjects: str = ""                         # ENIGMA_ADMIN_SUBJECTS: comma-separated verified user ids, e.g. cognito:<sub>. Empty = nobody is admin
    aws_ops: str = "off"                             # ENIGMA_AWS_OPS: off | mock (SYNTHETIC demo) | live (boto3 reads of the manifest resources)
    aws_manifest: str = ""                           # ENIGMA_AWS_MANIFEST: JSON allow-list of resource ids (see awsops.Manifest)
    aws_ops_cache_s: int = 60                        # ENIGMA_AWS_OPS_CACHE_S (floor 30): minimum seconds between AWS reads
    build_sha: str = ""                              # ENIGMA_BUILD_SHA
    environment: str = "dev"                         # ENIGMA_ENVIRONMENT
    # DynamoDB store (Track B). Table name only: credentials come from the Lambda execution role, never from settings.
    ddb_table: str = ""                              # env ENIGMA_DDB_TABLE (prefix-derived)
    # PostgreSQL (AWS RDS) store: libpq DSN. Server-only secret; with sslmode=verify-full for RDS. Never logged.
    db_dsn: str = ""                                 # env ENIGMA_DB_DSN (prefix-derived)
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
    # Neon Managed Better Auth (additive to Cognito: tokens are EdDSA, so they can never be confused with Cognito RS256 tokens).
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
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}" if self.cognito_region and self.cognito_user_pool_id else ""

    @property
    def cognito_jwks_url(self) -> str:
        return self.cognito_issuer + "/.well-known/jwks.json" if self.cognito_issuer else ""

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
        if self.auth_provider not in ("clerk", "cognito"):
            problems.append("ENIGMA_AUTH_PROVIDER must be 'clerk' or 'cognito'")
        if self.auth_provider == "cognito" and (self.cognito_region or self.cognito_user_pool_id):
            import re as _re
            if not (_re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d", self.cognito_region) and _re.fullmatch(r"[A-Za-z0-9-]+_[A-Za-z0-9]+", self.cognito_user_pool_id)):
                problems.append("COGNITO_REGION / COGNITO_USER_POOL_ID are malformed (they are used to build the JWKS URL)")
        if self.cognito_jwks_json:
            try:
                import json as _json
                keys = _json.loads(self.cognito_jwks_json).get("keys")
                if not (isinstance(keys, list) and keys and all(isinstance(k, dict) and k.get("kid") and k.get("kty") == "RSA" for k in keys)):
                    raise ValueError
            except ValueError:
                problems.append("COGNITO_JWKS_JSON must be a JWKS document with RSA keys that carry a kid")
        if self.neon_auth_base_url or self.neon_auth_jwks_url:
            if not self.neon_origin:
                problems.append("NEON_AUTH_BASE_URL must be an https URL (it pins the token issuer and audience)")
            elif self.neon_auth_jwks_url and not self.neon_auth_jwks_url.startswith(self.neon_origin + "/"):
                problems.append("NEON_AUTH_JWKS_URL must be on the same origin as NEON_AUTH_BASE_URL")
        if self.aws_ops not in ("off", "mock", "live"):
            problems.append("ENIGMA_AWS_OPS must be off, mock or live")
        if self.aws_ops == "mock" and self.is_production:
            problems.append("ENIGMA_AWS_OPS=mock (synthetic data) is refused in production")
        if self.aws_ops == "live":
            from .awsops import Manifest, ManifestError
            try:
                Manifest.parse(self.aws_manifest)
            except ManifestError as e:
                problems.append(f"ENIGMA_AWS_MANIFEST invalid: {e}")
            if not self.admin_subjects.strip():
                problems.append("ENIGMA_ADMIN_SUBJECTS is required when ENIGMA_AWS_OPS=live")
        if self.cognito_token_use not in ("access", "id"):
            problems.append("COGNITO_TOKEN_USE must be 'access' or 'id'")
        if self.dev_auth and (prod or self.env not in ("development", "test")):
            problems.append("ENIGMA_DEV_AUTH must not be enabled in production/Railway/non-dev environments")
        if self.dev_auth and len(self.dev_jwt_secret) < 32:
            problems.append("ENIGMA_DEV_JWT_SECRET must be at least 32 characters when dev auth is on")
        if prod:
            if self.store not in ("supabase", "postgres", "dynamodb"):
                problems.append("ENIGMA_STORE must be 'supabase', 'postgres' or 'dynamodb' in production (the memory store is not durable)")
            if self.store == "dynamodb":
                if not self.ddb_table:
                    problems.append("ENIGMA_DDB_TABLE is required when ENIGMA_STORE=dynamodb")
            elif self.store == "postgres":
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
