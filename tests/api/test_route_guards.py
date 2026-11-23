"""Every user-plane route must demand authentication and tenant membership. This walks the live OpenAPI schema so a route added
later cannot silently skip authorisation (the service role bypasses RLS, so app-level scoping is the real tenant boundary)."""
from __future__ import annotations

import pytest

UUID = "00000000-0000-0000-0000-000000000000"
OPEN = {"/api/v1/health", "/api/v1/admin/prune", "/api/v1/admin/ready"}            # health is public; prune and ready have their own bearer-secret check (tested elsewhere)
USER_ONLY_NO_TENANT = {"/api/v1/app/me", "/api/v1/app/workspaces"}          # act on the caller's own identity, no tenant id in the path


def user_routes(client):
    for path, ops in client.get("/api/v1/openapi.json").json()["paths"].items():
        if path.startswith("/api/v1/app/"):
            for method in ops:
                yield method.upper(), path


def valid_body(path: str) -> dict:
    """A schema-valid body per route: a 422 must never be allowed to stand in for an authorisation result."""
    if path.endswith("/jobs"):
        return {"runtime_id": UUID, "content": "hi"}
    if path.endswith(("/projects", "/runtimes")):
        return {"name": "x"}
    return {}


def fill(path: str) -> str:
    import re
    return re.sub(r"\{[^}]+\}", UUID, path)


def test_every_user_route_requires_authentication(client):
    seen = list(user_routes(client))
    assert len(seen) >= 20
    for method, path in seen:
        r = client.request(method, fill(path), json={} if method == "POST" else None)
        assert r.status_code == 401, f"{method} {path} answered {r.status_code} without credentials"


def test_every_tenant_route_hides_resources_from_non_members(client, alice, bob):
    """Bob holds a valid identity but no membership in Alice's workspace: nothing keyed by Alice's ids may be reachable."""
    ids = {"workspace_id": alice.workspace_id, "project_id": alice.project_id, "runtime_id": alice.runtime_id}
    conv = alice.conversation()
    job = alice.submit(conv).json()["job_id"]
    ids.update(conversation_id=conv, job_id=job, run_id=alice.run_id)
    import re
    checked = 0
    for method, path in user_routes(client):
        if path in USER_ONLY_NO_TENANT:
            continue
        concrete = re.sub(r"\{(\w+)\}", lambda m: ids.get(m.group(1), UUID), path)
        body = valid_body(path) if method == "POST" else None
        headers = {**bob.h, "Idempotency-Key": "guard-key-0001"}
        r = client.request(method, concrete, json=body, headers=headers)
        assert r.status_code in (403, 404), f"{method} {path} leaked to a non-member: {r.status_code}"
        checked += 1
    assert checked >= 18
