from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient

DEV_SECRET = "d" * 48


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, seconds: float):
        self.t += timedelta(seconds=seconds)


def dev_token(user_id: str, secret: str = DEV_SECRET, exp_in: int = 600) -> str:
    return jwt.encode({"sub": user_id, "aud": "enigma-dev", "exp": int(time.time()) + exp_in}, secret, algorithm="HS256")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Tenant:
    """A user with a workspace, project, and (optionally) a connected mock runtime."""

    def __init__(self, client: TestClient, user_id: str):
        self.c, self.user_id = client, user_id
        self.h = {"Authorization": f"Bearer {dev_token(user_id)}"}
        r = client.post("/api/v1/app/workspaces", json={"name": f"WS {user_id}"}, headers=self.h)
        assert r.status_code == 201, r.text
        self.workspace_id = r.json()["workspace"]["id"]
        self.project_id = r.json()["project"]["id"]

    def create_runtime(self, name="rt", is_mock=True):
        r = self.c.post(f"/api/v1/app/projects/{self.project_id}/runtimes", json={"name": name, "is_mock": is_mock}, headers=self.h)
        assert r.status_code == 201, r.text
        body = r.json()
        self.runtime_id = body["runtime"]["id"]
        self.token = body["credential"]["token"]
        self.rh = {"Authorization": f"Bearer {self.token}", "X-Enigma-Instance": "inst-1"}
        return body

    def register(self, is_mock=True, **over):
        payload = {"connector_version": "test", "is_mock": is_mock, "served_model": "mock-model", "tp": 1, "pp": 1, "max_model_len": 2048,
                   "gpus": [{"index": 0, "uuid": "GPU-MOCK-0", "name": "MOCK T4", "memory_total_mib": 15360}], **over}
        r = self.c.post("/api/v1/runtimes/register", json=payload, headers=self.rh)
        assert r.status_code == 200, r.text
        self.run_id = r.json()["run_id"]
        return r.json()

    def conversation(self):
        r = self.c.post(f"/api/v1/app/projects/{self.project_id}/conversations", json={"title": "t"}, headers=self.h)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def submit(self, conv_id, content="hello", key=None, **over):
        return self.c.post(f"/api/v1/app/conversations/{conv_id}/jobs", json={"runtime_id": self.runtime_id, "content": content, **over},
                           headers={**self.h, "Idempotency-Key": key or uuid.uuid4().hex})

    def claim(self):
        return self.c.post("/api/v1/jobs/claim", json={}, headers=self.rh)

    def events(self, job_id, events, headers=None):
        return self.c.post(f"/api/v1/jobs/{job_id}/events", json={"events": events}, headers=headers or self.rh)
