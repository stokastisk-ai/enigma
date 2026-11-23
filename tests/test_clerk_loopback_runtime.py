"""Regression: `next dev --hostname 127.0.0.1` with Clerk configured must answer, not proxy to itself (ECONNRESET / "socket hang up").

Root cause (2026-11-23, reproduced against the real server, see docs/ENIGMA_NEXT16_LOCAL_RUNTIME_REPAIR_REPORT.md):
  * Next's `NextURL` canonicalises every loopback IP to the string `localhost`, so `NextRequest.url` is `http://localhost:3000/...` even when the
    server is bound to and addressed as 127.0.0.1.
  * `@clerk/nextjs` `decorateRequest` answers a pass-through request with `x-middleware-rewrite: <req.url>` (same URL, plus decorated headers).
  * Next compares that rewrite's origin with the server's own origin (`http://127.0.0.1:3000`); they differ, so Next treats it as an EXTERNAL rewrite,
    proxies it to `http://localhost:3000/`, which re-enters the middleware and loops until the socket is reset after ~30 s.
Cognito and unconfigured modes never hit this path, which is why the older real-server suite (Cognito) stayed green.

The server runs on loopback only, with synthetic Clerk keys and no network access: signed-out requests are answered without contacting Clerk.
"""
from __future__ import annotations

import base64
import http.client
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
PORT = 3202

pytestmark = pytest.mark.skipif(not shutil.which("node") or not (WEB / "node_modules" / "next").exists(), reason="needs node and installed web dependencies")

# Synthetic, well-formed development keys: the publishable key encodes a fake frontend host; neither is a real Clerk instance.
PK = "pk_test_" + base64.b64encode(b"example-app-1.clerk.accounts.dev$").decode().rstrip("=")
SK = "sk_test_" + "A1b2C3d4" * 5


def _free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def get(path: str, host: str | None = None, timeout: float = 25.0, document: bool = False):
    """`document=False` is what curl, urllib and the smoke runner send (Accept: */*): it reproduces the loop. `document=True` mimics a browser navigation."""
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=timeout)
    headers = {"host": host or f"127.0.0.1:{PORT}", "accept": "text/html" if document else "*/*"}
    if document:
        headers["sec-fetch-dest"] = "document"
    conn.request("GET", path, headers=headers)
    r = conn.getresponse()
    body = r.read()
    out = (r.status, r.getheader("location"), body)
    conn.close()
    return out


@pytest.fixture(scope="module")
def clerk_server():
    if not _free(PORT):
        pytest.skip(f"port {PORT} busy")
    log = tempfile.NamedTemporaryFile("w+b", suffix=".log", delete=False)
    env = {
        "PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), "NODE_ENV": "development", "NEXT_TELEMETRY_DISABLED": "1",
        "ENIGMA_DIST_DIR": ".next-e2e", "CLERK_TELEMETRY_DISABLED": "1",
        "CLERK_SECRET_KEY": SK, "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": PK, "CLERK_PUBLISHABLE_KEY": PK,
        "NEXT_PUBLIC_CLERK_SIGN_IN_URL": "/sign-in", "NEXT_PUBLIC_CLERK_SIGN_UP_URL": "/sign-up",
        "ENIGMA_API_URL": "http://127.0.0.1:9",           # nothing listens here; any call to it must fail fast, never hang the page
    }
    proc = subprocess.Popen(["npx", "next", "dev", "-p", str(PORT), "-H", "127.0.0.1"], cwd=WEB, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        for _ in range(240):
            if proc.poll() is not None:
                pytest.fail("next dev exited early with code %s" % proc.returncode)
            try:
                socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
                break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail("next dev did not start")
        yield log.name
    finally:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                proc.wait(timeout=10)
                break
            except subprocess.TimeoutExpired:
                continue
        log.close()


def _log(path: str) -> str:
    return Path(path).read_text(errors="replace")


def test_public_root_answers_instead_of_proxying_to_itself(clerk_server):
    status, _, body = get("/")
    assert status == 200 and b"<html" in body.lower()
    assert "Failed to proxy" not in _log(clerk_server), "Clerk's same-URL rewrite must not be treated as an external proxy target"


@pytest.mark.parametrize("host", [f"127.0.0.1:{PORT}", f"localhost:{PORT}"])
def test_public_pages_answer_for_both_loopback_host_names(clerk_server, host):
    for path in ("/", "/sign-in", "/sign-up"):
        status, _, _ = get(path, host=host)
        assert status == 200, f"{path} via Host {host} -> {status}"


def test_browser_navigation_gets_clerk_dev_handshake_redirect_on_approved_host(clerk_server):
    status, location, _ = get("/", document=True)
    assert status in (200, 307), status
    if status == 307:
        assert location and location.startswith(("https://example-app-1.clerk.accounts.dev/", f"http://127.0.0.1:{PORT}/", f"http://localhost:{PORT}/")), "handshake redirect left the approved hosts"


def test_protected_pages_stay_protected_without_hanging(clerk_server):
    for path in ("/app", "/app/gpus", "/app/inference", "/app/playground", "/onboarding"):
        status, location, _ = get(path)
        assert status != 200, f"{path} must not be served to an anonymous visitor"
        assert status in (302, 303, 307, 308, 401, 404), f"{path} -> {status}"
        if location:
            assert location.startswith(("/", f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}", "https://example-app-1.clerk.accounts.dev")), "redirect escaped the approved origins"


def test_protected_api_route_is_not_open_without_a_session(clerk_server):
    status, _, body = get("/api/enigma/gpus")
    assert status in (307, 401, 404), status
    assert b"user_id" not in body


def test_no_secret_material_in_server_log(clerk_server):
    text = _log(clerk_server)
    assert SK not in text and "sk_test_" not in text
