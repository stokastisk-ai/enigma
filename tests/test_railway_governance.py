"""Source-only Railway adapter governance. No network, no Railway CLI, no cloud calls, no GPU."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from enigma_api.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
def _instructions(path: str) -> str:
    """Dockerfile text without comment lines, so a comment can explain a ban without tripping it."""
    return "\n".join(l for l in (ROOT / path).read_text().splitlines() if not l.lstrip().startswith("#"))


API_DF = _instructions("apps/api/Dockerfile")
WEB_DF = _instructions("apps/web/Dockerfile")
RAILWAY_ENV = ("RAILWAY_ENVIRONMENT_ID", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in RAILWAY_ENV:
        monkeypatch.delenv(k, raising=False)


def test_api_image_is_python313_cpu_only_nonroot_and_binds_port():
    assert re.search(r"^FROM python:3\.13[-\w.]*", API_DF, re.M)
    assert "USER 10001" in API_DF
    assert "--host 0.0.0.0" in API_DF and "${PORT" in API_DF
    assert "--factory" in API_DF and "enigma_api.main:create_app" in API_DF
    assert "ENIGMA_DEV_AUTH" not in API_DF and "--forwarded-allow-ips" not in API_DF
    reqs = (ROOT / "apps/api/requirements-railway.txt").read_text().lower()
    for banned in ("torch", "vllm", "stokastisk", "nvidia", "cuda", "pynvml", "moto", "mangum"):      # no GPU/inference stack, no test emulator, no Lambda adapter
        assert banned not in re.sub(r"#.*", "", reqs), banned
    assert "psycopg" in reqs and "uvicorn==" in reqs and "boto3==" in reqs                               # boto3 (pinned) for the DynamoDB store


def test_web_image_uses_repo_root_context_standalone_and_pins_node():
    assert re.search(r"^FROM node:24", WEB_DF, re.M) and ":latest" not in WEB_DF
    assert "packages/contracts" in WEB_DF            # the build imports ../../../packages/contracts/src/api, so the context must be the repo root
    assert "ENIGMA_STANDALONE=1" in WEB_DF and "server.js" in WEB_DF
    assert "HOSTNAME=0.0.0.0" in WEB_DF and "USER 10001" in WEB_DF
    assert "ENIGMA_DEV_AUTH" not in WEB_DF and "output: 'export'" not in WEB_DF
    assert "NODE_ENV=production" in WEB_DF


def test_dockerignores_deny_by_default_and_never_admit_secrets():
    for name in ("apps/api/Dockerfile.dockerignore", "apps/web/Dockerfile.dockerignore"):
        lines = [l.strip() for l in (ROOT / name).read_text().splitlines() if l.strip() and not l.startswith("#")]
        assert lines[0] == "*", name
        assert not any(l.startswith("!") and (".env" in l or ".enigma-local" in l or ".aws" in l) for l in lines), name
    assert ".env*" in (ROOT / "apps/web/Dockerfile.dockerignore").read_text()


def test_no_secret_values_in_railway_artifacts():
    pat = re.compile(r"(AKIA[0-9A-Z]{16}|sk_(live|test)_\w{10,}|pk_(live|test)_\w{10,}|postgres(ql)?://[^\s:]+:[^\s@]+@|-----BEGIN [A-Z ]*PRIVATE KEY)")
    for p in ("apps/api/Dockerfile", "apps/web/Dockerfile", "apps/api/requirements-railway.txt", ".dockerignore"):
        assert not pat.search((ROOT / p).read_text()), p


@pytest.mark.parametrize("var", RAILWAY_ENV)
def test_railway_platform_is_treated_as_production(monkeypatch, var):
    monkeypatch.setenv(var, "x")
    s = Settings(env="development", store="memory", dev_auth=True, dev_jwt_secret="d" * 40)
    assert s.on_railway and s.is_production
    problems = " | ".join(s.validate_for_startup())
    assert "ENIGMA_DEV_AUTH must not be enabled" in problems     # dev auth refused even though ENIGMA_ENV=development
    assert "memory store is not durable" in problems             # memory store refused


def test_off_railway_development_still_boots(monkeypatch):
    s = Settings(env="development", store="memory")
    assert not s.on_railway and not s.is_production and s.validate_for_startup() == []


def test_railway_postgres_production_config_validates(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    s = Settings(env="production", store="postgres", db_dsn="postgresql://u@h:5432/d?sslmode=verify-full",
                 neon_auth_base_url="https://ep-x.neonauth.us-east-2.aws.neon.tech/neondb/auth", cron_secret="c" * 32)
    assert s.validate_for_startup() == []
