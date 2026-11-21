from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api_helpers import DEV_SECRET, Clock, Tenant
from enigma_api.main import create_app
from enigma_api.realtime import RecordingBroadcaster
from enigma_api.settings import Settings
from enigma_api.store.memory import MemoryStore


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    return MemoryStore(clock)


@pytest.fixture
def bus():
    return RecordingBroadcaster()


@pytest.fixture
def settings():
    return Settings(env="test", dev_auth=True, dev_jwt_secret=DEV_SECRET, cron_secret="c" * 32)


@pytest.fixture
def client(settings, store, bus, clock):
    app = create_app(settings, store=store, broadcaster=bus, clock=clock)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def alice(client):
    t = Tenant(client, "user_alice")
    t.create_runtime()
    t.register()
    return t


@pytest.fixture
def bob(client):
    t = Tenant(client, "user_bob")
    t.create_runtime()
    t.register()
    return t
