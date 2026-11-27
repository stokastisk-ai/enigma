"""Owner-run helper for the ISOLATED AWS staging integration (notebook 03, INTEGRATION_MODE = "aws-staging").

The AWS runtime endpoint (Lambda ``enigma-runtime-api-staging``) reads and writes ONLY the staging table ``enigma-rt-staging``; production (Railway +
Neon) never sees these rows and this script never touches production. It runs the SAME FastAPI app LOCALLY (dev auth, test env, the caller's
``enigma-deployer`` SSO credentials) to do what the web Playground would do on the staging data:

    AWS_PROFILE=enigma-deployer python3.13 scripts/aws_staging_runtime.py provision     # one REAL (non-mock) staging runtime + its runtime token
    AWS_PROFILE=enigma-deployer python3.13 scripts/aws_staging_runtime.py submit --prompt "Count from one to ten in words."
    AWS_PROFILE=enigma-deployer python3.13 scripts/aws_staging_runtime.py status
    AWS_PROFILE=enigma-deployer python3.13 scripts/aws_staging_runtime.py revoke

``provision`` writes the runtime token to ``.enigma-local/ENIGMA_STAGING_RUNTIME_TOKEN.txt`` (mode 0600, git-excluded) and NEVER prints it; paste
that file's content as the Kaggle Secret ``ENIGMA_RUNTIME_TOKEN`` for a staging run only (a production run needs the production runtime token).
``submit`` must be run while notebook 03's full-connector stage is running: it submits one Playground job to the staging runtime, waits for the Kaggle
GPU to claim, stream and complete it, and prints the job status, event count and reply. Staging acceptance is NOT production acceptance."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "tests" / "api")]
TABLE = "enigma-rt-staging"
LOCAL = ROOT / ".enigma-local"
TOKEN_FILE = LOCAL / "ENIGMA_STAGING_RUNTIME_TOKEN.txt"
STATE_FILE = LOCAL / "aws-staging-runtime.json"
OWNER = "staging_owner"                                 # a dev identity that exists only in the staging table


def _client():
    from fastapi.testclient import TestClient
    from api_helpers import DEV_SECRET
    from enigma_api.main import create_app
    from enigma_api.settings import Settings
    from enigma_api.store.dynamodb import DynamoStore
    for k in [k for k in os.environ if k.startswith("RAILWAY_")]:
        os.environ.pop(k)                               # never mistaken for a Railway (production) process
    app = create_app(Settings(env="test", dev_auth=True, dev_jwt_secret=DEV_SECRET, store="dynamodb", ddb_table=TABLE),
                     store=DynamoStore(TABLE, region="us-east-1"))
    return TestClient(app, raise_server_exceptions=False)


def _h():
    from api_helpers import dev_token
    return {"Authorization": f"Bearer {dev_token(OWNER, exp_in=3600)}"}


def _state() -> dict:
    if not STATE_FILE.exists():
        sys.exit("not provisioned: run `provision` first")
    return json.loads(STATE_FILE.read_text())


def provision(c, rotate: bool) -> int:
    if STATE_FILE.exists() and not rotate:
        print(json.dumps({"provisioned": True, "note": "already provisioned; use --rotate to replace the runtime and its token", **_state()}, indent=1))
        return 0
    r = c.post("/api/v1/app/workspaces", json={"name": "AWS staging (isolated)"}, headers=_h())
    assert r.status_code == 201, r.status_code
    ws, proj = r.json()["workspace"]["id"], r.json()["project"]["id"]
    r = c.post(f"/api/v1/app/projects/{proj}/runtimes", json={"name": "kaggle-2xT4-staging", "is_mock": False}, headers=_h())
    assert r.status_code == 201, r.status_code
    body = r.json()
    token = body["credential"]["token"]
    LOCAL.mkdir(exist_ok=True)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    st = {"table": TABLE, "workspace_id": ws, "project_id": proj, "runtime_id": body["runtime"]["id"], "is_mock": False,
          "token_file": str(TOKEN_FILE.relative_to(ROOT)), "token_chars": len(token)}
    STATE_FILE.write_text(json.dumps(st, indent=1))
    print(json.dumps({"provisioned": True, **st}, indent=1))       # ids only; the token itself is never printed
    return 0


def submit(c, prompt: str, wait_s: float, poll_s: float = 3.0) -> int:
    st = _state()
    r = c.post(f"/api/v1/app/projects/{st['project_id']}/conversations", json={"title": "staging playground"}, headers=_h())
    assert r.status_code == 201, r.status_code
    conv = r.json()["id"]
    r = c.post(f"/api/v1/app/conversations/{conv}/jobs", json={"runtime_id": st["runtime_id"], "content": prompt},
               headers={**_h(), "Idempotency-Key": uuid.uuid4().hex})
    if r.status_code != 202:
        print(json.dumps({"submitted": False, "http": r.status_code, "error": r.json(), "VERDICT": "STAGING_PLAYGROUND_JOB_NOT_SUBMITTED",
                          "hint": "submit only while notebook 03's full-connector stage is running (the runtime must be online)"}, indent=1))
        return 1
    job_id, t0, last = r.json()["job_id"], time.monotonic(), None
    print(json.dumps({"submitted": True, "job_id": job_id, "data_plane": "STAGING (enigma-rt-staging)"}), flush=True)
    while time.monotonic() - t0 < wait_s:
        j = c.get(f"/api/v1/app/jobs/{job_id}?after_seq=0", headers=_h()).json()
        if j.get("status") != last:
            last = j.get("status")
            print(json.dumps({"t_s": round(time.monotonic() - t0, 1), "status": last, "events": j.get("last_event_seq")}), flush=True)
        if last in ("succeeded", "failed", "cancelled", "expired"):
            break
        time.sleep(poll_s)
    msgs = c.get(f"/api/v1/app/conversations/{conv}/messages", headers=_h()).json().get("items", [])
    reply = next((m["content"] for m in reversed(msgs) if m["role"] == "assistant"), None)
    ok = last == "succeeded" and bool((reply or "").strip())
    print(json.dumps({"job_id": job_id, "status": last, "assistant_reply": reply, "persisted_messages": [m["role"] for m in msgs],
                      "VERDICT": "STAGING_PLAYGROUND_JOB_PASS" if ok else "STAGING_PLAYGROUND_JOB_NOT_COMPLETED"}, indent=1))
    return 0 if ok else 1


def status(c) -> int:
    st = _state()
    r = c.get(f"/api/v1/app/projects/{st['project_id']}/runtimes", headers=_h()).json()
    print(json.dumps({"state": st, "runtimes": r}, indent=1, default=str))
    return 0


def revoke(c) -> int:
    st = _state()
    r = c.post(f"/api/v1/app/runtimes/{st['runtime_id']}/revoke", headers=_h())
    print(json.dumps({"revoked": r.status_code == 200, "http": r.status_code}))
    return 0 if r.status_code == 200 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("provision").add_argument("--rotate", action="store_true")
    s = sp.add_parser("submit")
    s.add_argument("--prompt", default="Count from one to ten in words.")
    s.add_argument("--wait-s", type=float, default=600)
    sp.add_parser("status"); sp.add_parser("revoke")
    a = ap.parse_args()
    if os.environ.get("AWS_PROFILE") != "enigma-deployer":
        sys.exit("set AWS_PROFILE=enigma-deployer (never the default/root profile)")
    c = _client()
    return {"provision": lambda: provision(c, a.rotate), "submit": lambda: submit(c, a.prompt, a.wait_s), "status": lambda: status(c),
            "revoke": lambda: revoke(c)}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())
