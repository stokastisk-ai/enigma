"""One-command smoke test of the transport: user -> API -> job -> connector (MOCK) -> events -> final message -> metrics.

    python3.13 scripts/smoke_e2e.py

Starts the real FastAPI app (in-memory store) and the labelled mock connector as subprocesses, runs one chat, prints measured values,
then stops both. Everything it reports is SYNTHETIC. Exit code 0 only if the whole path worked."""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = secrets.token_hex(32)          # throwaway per run; never a committed value


def call(base, method, path, body=None, headers=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def main() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "ENIGMA_ENV": "development", "ENIGMA_DEV_AUTH": "true", "ENIGMA_DEV_JWT_SECRET": SECRET, "PYTHONDONTWRITEBYTECODE": "1"}
    api = subprocess.Popen([sys.executable, "-m", "uvicorn", "index:app", "--app-dir", str(ROOT / "apps" / "api"), "--port", str(port), "--log-level", "warning"], env=env)
    conn = None
    try:
        for _ in range(100):
            try:
                call(base, "GET", "/api/v1/health"); break
            except OSError:
                time.sleep(0.1)
        import jwt  # noqa: F401  (token minting uses the same secret as the API)
        tok = dev_token_with(SECRET)
        H = {"Authorization": f"Bearer {tok}"}
        ws = call(base, "POST", "/api/v1/app/workspaces", {"name": "Smoke"}, H)
        pid = ws["project"]["id"]
        rt = call(base, "POST", f"/api/v1/app/projects/{pid}/runtimes", {"name": "smoke-mock", "is_mock": True}, H)
        conn = subprocess.Popen([sys.executable, "-m", "enigma_connector", "--mode", "mock"], cwd=ROOT / "connector",
                                env={**env, "ENIGMA_API_URL": base, "ENIGMA_RUNTIME_TOKEN": rt["credential"]["token"], "ENIGMA_HEARTBEAT_S": "1"})
        for _ in range(100):
            rts = call(base, "GET", f"/api/v1/app/projects/{pid}/runtimes", headers=H)["items"]
            if rts and rts[0]["status"] == "online":
                break
            time.sleep(0.2)
        else:
            print("FAIL: runtime never came online"); return 1
        conv = call(base, "POST", f"/api/v1/app/projects/{pid}/conversations", {}, H)["id"]
        job = call(base, "POST", f"/api/v1/app/conversations/{conv}/jobs", {"runtime_id": rt["runtime"]["id"], "content": "smoke test prompt"}, {**H, "Idempotency-Key": "smoke-key-0001"})
        for _ in range(100):
            v = call(base, "GET", f"/api/v1/app/jobs/{job['job_id']}", headers=H)
            if v["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.2)
        msgs = call(base, "GET", f"/api/v1/app/conversations/{conv}/messages", headers=H)["items"]
        time.sleep(6)
        gpu = call(base, "GET", f"/api/v1/app/projects/{pid}/metrics/gpu?range=5m", headers=H)
        ok = v["status"] == "succeeded" and msgs[-1]["role"] == "assistant" and set(gpu["series"]) == {"0", "1"} and gpu["provenance"]["measured_rows"] == 0
        print(json.dumps({"job_status": v["status"], "reply": msgs[-1]["content"][:60], "measured": v["result"], "gpus": sorted(gpu["series"]),
                          "provenance": gpu["provenance"], "label": "ALL SYNTHETIC (mock connector)"}, indent=2))
        print("SMOKE OK" if ok else "SMOKE FAILED")
        return 0 if ok else 1
    finally:
        for p in (conn, api):
            if p:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()


def dev_token_with(secret: str) -> str:
    import jwt
    return jwt.encode({"sub": "smoke-user", "aud": "enigma-dev", "exp": int(time.time()) + 600}, secret, algorithm="HS256")


if __name__ == "__main__":
    raise SystemExit(main())
