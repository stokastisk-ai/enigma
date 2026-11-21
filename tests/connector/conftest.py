from __future__ import annotations

import urllib.request

import httpx
import pytest

from api_helpers import Tenant
from enigma_connector.config import Config
from enigma_connector.http import ApiClient
from live_server import LiveApi


@pytest.fixture
def live():
    with LiveApi(real_clock=True) as api:
        yield api


@pytest.fixture
def tenant(live):
    """A tenant with a MOCK runtime credential; the helper client talks to the live server over real HTTP."""
    class Http:                                            # TestClient-compatible subset over real HTTP (for the Tenant helper)
        def __init__(self, base): self.c = httpx.Client(base_url=base, timeout=10)
        def post(self, url, **kw): return self.c.post(url, **kw)
        def get(self, url, **kw): return self.c.get(url, **kw)
    t = Tenant(Http(live.url), "user_conn")
    t.create_runtime()
    return t


@pytest.fixture
def cfg(live, tenant):
    return Config(api_url=live.url, token=tenant.token, instance_id="test-inst", mode="mock", served_model="mock-model",
                  flush_interval_s=0.02, keepalive_s=0.3, claim_poll_s=0.05, heartbeat_interval_s=0.2, gpu_interval_s=0.05,
                  vllm_interval_s=0.05, upload_interval_s=0.2, lease_seconds=30).validate()


@pytest.fixture
def api(cfg):
    return ApiClient(cfg)
