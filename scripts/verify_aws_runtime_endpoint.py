"""Live, bounded contract verification of the OPTIONAL AWS runtime endpoint (Lambda Function URL running the same FastAPI app) against its
ISOLATED staging table. Real AWS, no mocks; no LLM inference (the job text is synthetic and the runtime is registered as a MOCK runtime).

    AWS_PROFILE=enigma-deployer python3.13 scripts/verify_aws_runtime_endpoint.py --url https://<id>.lambda-url.us-east-1.on.aws

1. Seeds two isolated tenants (workspace, project, mock runtime, credential) in table ``enigma-rt-staging`` by running the app LOCALLY (dev auth,
   test env, the caller's SSO credentials): the same code path the web app uses; tokens stay in memory and are never printed.
2. Over HTTPS to the Lambda: health, register, heartbeat, telemetry batch (+ idempotent replay), job claim (+ no double claim), ordered events
   (+ duplicate), status, completion (+ idempotent replay and conflicting-hash 409), tenant isolation, a bogus runtime key and a diagnostic-shaped key
   (401), then credential revocation (401 afterwards).
3. Prints only status codes and booleans."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "tests" / "api")]
TABLE = "enigma-rt-staging"


def call(url, method, path, body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    h = {"Accept": "application/json", "User-Agent": "Enigma-Runtime-Verify/1.0"}
    if data is not None:
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = f"Bearer {token}"
        h["X-Enigma-Instance"] = "verify-1"
    req = urllib.request.Request(url.rstrip("/") + path, data=data, method=method, headers=h)
    t = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read(1 << 20) or b"null"), round(time.monotonic() - t, 3)
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read(65536) or b"null")
        except ValueError:
            j = None
        return e.code, j, round(time.monotonic() - t, 3)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True)
    url = ap.parse_args().url
    from fastapi.testclient import TestClient
    from api_helpers import DEV_SECRET, Tenant
    from enigma_api.main import create_app
    from enigma_api.settings import Settings
    from enigma_api.store.dynamodb import DynamoStore
    for k in [k for k in os.environ if k.startswith(("RAILWAY_",))]:
        os.environ.pop(k)
    local = create_app(Settings(env="test", dev_auth=True, dev_jwt_secret=DEV_SECRET, store="dynamodb", ddb_table=TABLE),
                       store=DynamoStore(TABLE, region="us-east-1"))
    web = TestClient(local, raise_server_exceptions=False)
    tag = uuid.uuid4().hex[:8]
    alice, bob = Tenant(web, f"verify_alice_{tag}"), Tenant(web, f"verify_bob_{tag}")
    alice.create_runtime(name="verify-mock", is_mock=True); bob.create_runtime(name="verify-mock", is_mock=True)
    R: dict = {}
    s, h, lat = call(url, "GET", "/api/v1/health")
    R["health"] = {"http": s, "latency_s": lat, **(h or {})}
    gpus = [{"index": 0, "uuid": "GPU-MOCK-0", "name": "MOCK T4", "memory_total_mib": 15360}]
    reg = {"connector_version": "verify", "is_mock": True, "served_model": "mock-model", "tp": 1, "pp": 1, "max_model_len": 2048, "gpus": gpus}
    s, me, _ = call(url, "GET", "/api/v1/runtimes/me", None, alice.token)
    R["runtimes_me"] = {"http": s, "same_runtime": (me or {}).get("runtime_id") == alice.runtime_id, "is_mock": (me or {}).get("is_mock")}
    s, b, lat = call(url, "POST", "/api/v1/runtimes/register", reg, alice.token)
    run_id = (b or {}).get("run_id")
    R["register"] = {"http": s, "latency_s": lat, "run_id": bool(run_id)}
    call(url, "POST", "/api/v1/runtimes/register", reg, bob.token)
    s, _, lat = call(url, "POST", f"/api/v1/runtimes/{alice.runtime_id}/heartbeat", {"run_id": run_id}, alice.token)
    R["heartbeat"] = {"http": s, "latency_s": lat}
    now = datetime.now(timezone.utc).isoformat()
    batch = {"batch_id": f"verify-{tag}-1", "run_id": run_id, "gpu": [{"captured_at": now, "gpu_index": 0, "util_gpu_pct": 1.0, "mem_used_mib": 10.0}],
             "vllm": [{"captured_at": now, "num_requests_running": 0}]}
    s1, b1, _ = call(url, "POST", "/api/v1/telemetry/batch", batch, alice.token)
    s2, b2, _ = call(url, "POST", "/api/v1/telemetry/batch", batch, alice.token)
    R["telemetry"] = {"http": s1, "rows": (b1 or {}).get("gpu_rows"), "replay_http": s2, "replay_duplicate": (b2 or {}).get("duplicate")}
    conv = alice.conversation()
    job_id = alice.submit(conv, "verify").json()["job_id"]
    s, b, lat = call(url, "POST", "/api/v1/jobs/claim", {}, alice.token)
    claimed = (b or {}).get("job") or {}
    s_again, b_again, _ = call(url, "POST", "/api/v1/jobs/claim", {}, alice.token)
    s_bob, b_bob, _ = call(url, "POST", "/api/v1/jobs/claim", {}, bob.token)
    R["claim"] = {"http": s, "same_job": claimed.get("job_id") == job_id, "second_claim_empty": (b_again or {}).get("job") is None,
                  "other_tenant_empty": s_bob == 200 and (b_bob or {}).get("job") is None, "latency_s": lat}
    ev = [{"event_seq": 1, "kind": "delta", "text": "ver"}, {"event_seq": 2, "kind": "delta", "text": "ified"}]
    s, b, _ = call(url, "POST", f"/api/v1/jobs/{job_id}/events", {"events": ev}, alice.token)
    sd, bd, _ = call(url, "POST", f"/api/v1/jobs/{job_id}/events", {"events": ev[1:]}, alice.token)
    R["events"] = {"http": s, "accepted": (b or {}).get("accepted"), "dup_http": sd, "duplicates": (bd or {}).get("duplicates")}
    s, b, _ = call(url, "GET", f"/api/v1/jobs/{job_id}/status", None, alice.token)
    R["status"] = {"http": s, "cancel_requested": (b or {}).get("cancel_requested")}
    sha = hashlib.sha256(b"verified").hexdigest()
    done = {"final_seq": 2, "text_sha256": sha, "result": {"stream_chunks": 2, "is_mock": True}}
    s, b, _ = call(url, "POST", f"/api/v1/jobs/{job_id}/complete", done, alice.token)
    s2, b2, _ = call(url, "POST", f"/api/v1/jobs/{job_id}/complete", done, alice.token)
    s3, _, _ = call(url, "POST", f"/api/v1/jobs/{job_id}/complete", {**done, "text_sha256": "0" * 64}, alice.token)
    s4, _, _ = call(url, "POST", f"/api/v1/jobs/{job_id}/complete", done, bob.token)
    msgs = web.get(f"/api/v1/app/conversations/{conv}/messages", headers=alice.h).json()["items"]
    R["complete"] = {"http": s, "replay_idempotent": (b2 or {}).get("idempotent"), "conflict_http": s3, "other_tenant_http": s4,
                     "assistant_persisted": [m["role"] for m in msgs] == ["user", "assistant"] and msgs[-1]["content"] == "verified"}
    R["auth"] = {"bogus_runtime_key": call(url, "POST", "/api/v1/jobs/claim", {}, "enig_rt_abcd1234_" + "x" * 43)[0],
                 "diagnostic_shaped_key": call(url, "POST", "/api/v1/jobs/claim", {}, "edx_abcd1234_" + "x" * 43)[0],
                 "no_key": call(url, "POST", "/api/v1/jobs/claim", {})[0], "me_without_key": call(url, "GET", "/api/v1/runtimes/me")[0]}
    for t in (alice, bob):
        web.post(f"/api/v1/app/runtimes/{t.runtime_id}/revoke", headers=t.h)
    R["auth"]["after_revocation"] = call(url, "POST", "/api/v1/jobs/claim", {}, alice.token)[0]
    ok = (R["health"]["http"] == 200 and R["health"].get("deployment") == "aws-staging-isolated" and R["health"].get("api_contract") == 2
          and R["health"].get("data_plane") == hashlib.sha256(f"dynamodb:{TABLE}".encode()).hexdigest()[:16]
          and R["runtimes_me"] == {"http": 200, "same_runtime": True, "is_mock": True} and R["register"]["http"] == 200
          and R["heartbeat"]["http"] == 200 and R["telemetry"]["http"] == 200 and R["telemetry"]["replay_duplicate"] is True
          and R["claim"]["same_job"] and R["claim"]["second_claim_empty"] and R["claim"]["other_tenant_empty"] and R["events"]["accepted"] == 2
          and R["events"]["duplicates"] == 1 and R["complete"]["http"] == 200 and R["complete"]["replay_idempotent"] is True
          and R["complete"]["conflict_http"] == 409 and R["complete"]["other_tenant_http"] == 404 and R["complete"]["assistant_persisted"]
          and R["auth"] == {"bogus_runtime_key": 401, "diagnostic_shaped_key": 401, "no_key": 401, "me_without_key": 401, "after_revocation": 401})
    R["VERDICT"] = "VERIFIED (isolated staging contract; not production, not inference)" if ok else "FAILED"
    out = json.dumps(R, indent=1, default=str)
    assert alice.token not in out and bob.token not in out
    print(out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
