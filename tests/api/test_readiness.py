"""Readiness (/api/v1/admin/ready) vs liveness (/api/v1/health). CPU-only, in-process, no network."""
from __future__ import annotations

from fastapi.testclient import TestClient

from api_helpers import DEV_SECRET
from enigma_api.main import create_app
from enigma_api.settings import Settings
from enigma_api.store.memory import MemoryStore
from enigma_api.store.supabase import StoreUnavailable

SECRET = "c" * 32
AUTH = {"Authorization": "Bearer " + SECRET}


def _client(store) -> TestClient:
    s = Settings(env="test", dev_auth=True, dev_jwt_secret=DEV_SECRET, cron_secret=SECRET, store="memory")
    return TestClient(create_app(s, store=store), raise_server_exceptions=False)


class DownStore(MemoryStore):
    def ready(self):
        raise StoreUnavailable("database unavailable: OperationalError")


def test_ready_requires_the_secret_and_never_reveals_state_to_anonymous_callers():
    c = _client(MemoryStore())
    assert c.get("/api/v1/admin/ready").status_code == 401
    assert c.get("/api/v1/admin/ready", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.get("/api/v1/admin/ready", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"ready": True, "store": "memory"}


def test_ready_is_503_when_the_database_is_down_but_liveness_stays_200():
    c = _client(DownStore())
    r = c.get("/api/v1/admin/ready", headers=AUTH)
    assert r.status_code == 503 and "OperationalError" not in r.text          # no driver detail leaked
    assert c.get("/api/v1/health").status_code == 200                           # liveness must not depend on the database


def test_ready_without_configured_secret_is_always_refused():
    s = Settings(env="test", dev_auth=True, dev_jwt_secret=DEV_SECRET, cron_secret="", store="memory")
    c = TestClient(create_app(s, store=MemoryStore()), raise_server_exceptions=False)
    assert c.get("/api/v1/admin/ready", headers={"Authorization": "Bearer "}).status_code == 401
