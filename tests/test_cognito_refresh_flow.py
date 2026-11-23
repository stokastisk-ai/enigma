"""Real request/cookie traces of the Cognito session flow through the Next.js 16 server (`next dev`, loopback only).

Why `next dev`: the web app honours ENIGMA_COGNITO_TEST_ENDPOINT only outside production, so a fake IDP (RS256 + JWKS, generated here) can stand in for
Cognito without any AWS call. A tiny RFC 6265 cookie jar (Path-scoped, Max-Age aware) plays the browser, so what is asserted is what a browser would
actually send. Expired tokens are simulated with token `exp` values and by letting the jar drop the access cookie: no sleeping, no wall-clock races.

Covers the 2026-11-23 defect: the refresh cookie is scoped to Path=/api, so page navigations (/app/...) never carry it and, once the 1 h access token
expired, every hard reload bounced to /sign-in although the session was still refreshable.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
POOL, CLIENT, REGION = "us-east-1_FakePool1", "fakeclientid1234567890", "us-east-1"
ISS = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
WEB_PORT = 3201

pytestmark = pytest.mark.skipif(not shutil.which("node") or not (WEB / "node_modules" / "next").exists(), reason="needs node and installed web dependencies")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


class FakeCognito:
    """Minimal IDP: InitiateAuth (password + refresh), RevokeToken, JWKS. Behaviour switches are plain attributes the tests flip."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        nums = self.key.public_key().public_numbers()
        self.jwks = {"keys": [{"kty": "RSA", "alg": "RS256", "use": "sig", "kid": "k1", "n": _b64(nums.n.to_bytes(256, "big")), "e": _b64(nums.e.to_bytes(3, "big"))}]}
        self.access_exp_delta = 3600       # seconds from now; negative = hand out an already-expired token (cookie stays, token is dead)
        self.refresh_ok = True
        self.calls: list[str] = []
        self.refresh_token = "rt-" + "x" * 40
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401
                pass

            def _send(self, code: int, body: dict) -> None:
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/x-amz-json-1.1")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):  # noqa: N802
                self._send(200, outer.jwks) if self.path.endswith("/jwks.json") else self._send(404, {})

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                target = self.headers.get("x-amz-target", "").rsplit(".", 1)[-1]
                outer.calls.append(target + ":" + body.get("AuthFlow", ""))
                if target == "RevokeToken":
                    return self._send(200, {})
                if target != "InitiateAuth":
                    return self._send(400, {"__type": "x#Unsupported"})
                flow = body.get("AuthFlow")
                if flow == "REFRESH_TOKEN_AUTH":
                    if not outer.refresh_ok or body["AuthParameters"].get("REFRESH_TOKEN") != outer.refresh_token:
                        return self._send(400, {"__type": "com.amazonaws#NotAuthorizedException"})
                    return self._send(200, {"AuthenticationResult": {"AccessToken": outer.mint(), "ExpiresIn": 3600}})
                if flow == "USER_PASSWORD_AUTH":
                    return self._send(200, {"AuthenticationResult": {"AccessToken": outer.mint(), "RefreshToken": outer.refresh_token, "ExpiresIn": 3600}})
                return self._send(400, {"__type": "x#Unsupported"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def mint(self, exp_delta: int | None = None) -> str:
        now = int(time.time())
        head = _b64(json.dumps({"alg": "RS256", "kid": "k1", "typ": "JWT"}).encode())
        body = _b64(json.dumps({"sub": "11111111-2222-3333-4444-555555555555", "iss": ISS, "token_use": "access", "client_id": CLIENT, "iat": now,
                                "exp": now + (self.access_exp_delta if exp_delta is None else exp_delta)}).encode())
        sig = self.key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{body}.{_b64(sig)}"


class FakeApi:
    def __init__(self) -> None:
        self.auth_headers: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):  # noqa: N802
                outer.auth_headers.append(self.headers.get("authorization", ""))
                raw = b'{"user_id":"fake"}'
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


class Browser:
    """No-redirect HTTP client + RFC 6265 cookie jar: a cookie is sent only when the request path matches its Path, and Max-Age=0 deletes it."""

    def __init__(self, port: int) -> None:
        self.port, self.jar, self.trace = port, {}, []

    def _cookie_header(self, path: str) -> str:
        pairs = [f"{n}={v}" for n, (v, cp, _) in self.jar.items() if cp == "/" or path == cp or path.startswith(cp.rstrip("/") + "/")]
        return "; ".join(pairs)

    def request(self, method: str, url: str, body: dict | None = None, origin: bool = True, headers: dict | None = None):
        parts = urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        h = {"host": f"127.0.0.1:{self.port}", **(headers or {})}
        ck = self._cookie_header(parts.path)
        if ck:
            h["cookie"] = ck
        if body is not None:
            h["content-type"] = "application/json"
        if method == "POST" and origin:
            h["origin"] = f"http://127.0.0.1:{self.port}"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = conn.getresponse()
        data = r.read()
        raw_cookies = r.headers.get_all("set-cookie") or []
        attrs = {}
        for line in raw_cookies or []:
            c = SimpleCookie()
            c.load(line)
            for name, m in c.items():
                attrs[name] = {k: m[k] for k in ("path", "max-age", "samesite", "httponly", "secure") if m[k] != ""}
                if m["max-age"] == "0" or m.value == "":
                    self.jar.pop(name, None)
                else:
                    self.jar[name] = (m.value, m["path"] or "/", attrs[name])
        out = {"status": r.status, "location": r.getheader("location"), "cookies": attrs, "cache_control": r.getheader("cache-control"), "body": data, "sent_cookie": ck}
        self.trace.append((method, path, out["status"], out["location"], sorted(attrs), "cookie=" + ",".join(p.split("=")[0] for p in ck.split("; ") if p)))
        conn.close()
        return out

    def sign_in(self):
        return self.request("POST", "/api/auth/sign-in", {"email": "a@example.test", "password": "correct-horse-1"})

    def follow(self, url: str, limit: int = 6):
        """GET with manual redirect following (same host only), as a browser navigation would."""
        for _ in range(limit):
            r = self.request("GET", url)
            if r["status"] not in (301, 302, 303, 307, 308):
                return r
            loc = urlsplit(r["location"])
            assert loc.hostname in (None, "127.0.0.1"), f"redirect left the origin: {r['location']}"
            url = loc.path + (f"?{loc.query}" if loc.query else "")
        raise AssertionError("redirect loop: " + " -> ".join(f"{t[0]} {t[1]} {t[2]}" for t in self.trace[-limit:]))


@pytest.fixture(scope="module")
def stack():
    if not _free(WEB_PORT):
        pytest.skip(f"port {WEB_PORT} busy")
    idp, api = FakeCognito(), FakeApi()
    env = {
        "PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), "NODE_ENV": "development", "NEXT_TELEMETRY_DISABLED": "1",
        "ENIGMA_DIST_DIR": ".next-e2e", "ENIGMA_AUTH_PROVIDER": "cognito",
        "NEXT_PUBLIC_COGNITO_REGION": REGION, "NEXT_PUBLIC_COGNITO_USER_POOL_ID": POOL, "NEXT_PUBLIC_COGNITO_CLIENT_ID": CLIENT,
        "ENIGMA_COGNITO_TEST_ENDPOINT": f"http://127.0.0.1:{idp.port}", "ENIGMA_API_URL": f"http://127.0.0.1:{api.port}",
        "ENIGMA_PUBLIC_HOST": f"127.0.0.1:{WEB_PORT}", "ENIGMA_PUBLIC_ORIGIN": f"http://127.0.0.1:{WEB_PORT}",
    }
    proc = subprocess.Popen(["npx", "next", "dev", "-p", str(WEB_PORT), "-H", "127.0.0.1"], cwd=WEB, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        for _ in range(240):
            try:
                probe = http.client.HTTPConnection("127.0.0.1", WEB_PORT, timeout=2)
                try:
                    probe.request("GET", "/")
                finally:
                    probe.close()
                break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail("next dev did not start")
        yield idp, api
    finally:                                                          # npx forks next-server: signal the whole process group so nothing is orphaned
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


@pytest.fixture()
def br(stack):
    idp, _ = stack
    idp.access_exp_delta, idp.refresh_ok, idp.calls[:] = 3600, True, []
    b = Browser(WEB_PORT)
    yield b
    if os.environ.get("ENIGMA_TRACE"):                                # opt-in: ENIGMA_TRACE=1 pytest -s ... prints every hop of every scenario
        print("\n--- trace:", os.environ.get("PYTEST_CURRENT_TEST", "").split("::")[-1].split(" ")[0])
        for m, path, status, loc, setc, sent in b.trace:
            print(f"  {m:4} {path:34} -> {status} {loc or '':36} set-cookie={setc} {sent}")


# ---------------------------------------------------------------- cookie attributes (what the browser is told)
def test_signin_sets_httponly_samesite_cookies_with_minimal_path_scope(br):
    r = br.sign_in()
    assert r["status"] == 200 and r["cache_control"] == "no-store"
    at, rt = r["cookies"]["enigma_at"], r["cookies"]["enigma_rt"]
    assert at["path"] == "/" and rt["path"] == "/api", "refresh token must stay scoped to /api; widening it would send it with every page and asset request"
    for c in (at, rt):
        assert c["httponly"] and c["samesite"].lower() == "lax"
    assert "secure" not in at, "dev http only; production Secure is asserted in the node unit tests"
    assert b"rt-" not in r["body"] and b"eyJ" not in r["body"], "tokens must never be in a response body"


# ---------------------------------------------------------------- the defect: reload after the access token is gone
def test_reload_after_access_cookie_expiry_refreshes_instead_of_bouncing_to_signin(br):
    br.sign_in()
    assert br.follow("/app/gpus")["status"] == 200
    br.jar.pop("enigma_at")                                           # what the browser does at Max-Age expiry (~1 h)
    n = len(br.trace)
    r = br.follow("/app/gpus")
    assert r["status"] == 200 and br.trace[-1][1] == "/app/gpus", "hard reload with a refreshable session must NOT end at /sign-in\n" + "\n".join(map(str, br.trace[n:]))
    assert "enigma_at" in br.jar and br.jar["enigma_at"][1] == "/"


def test_reload_with_expired_but_present_access_token_refreshes(br, stack):
    idp, _ = stack
    idp.access_exp_delta = -30                                        # sign-in hands out a token that is already dead; the cookie itself is still alive
    br.sign_in()
    idp.access_exp_delta = 3600                                       # the refresh will return a fresh one
    n = len(br.trace)
    r = br.follow("/app/runs")
    assert r["status"] == 200 and br.trace[-1][1] == "/app/runs", "\n".join(map(str, br.trace[n:]))
    assert "REFRESH_TOKEN_AUTH" in "".join(idp.calls)


def test_refresh_token_is_never_sent_to_page_or_asset_requests(br):
    br.sign_in()
    br.follow("/app/gpus")
    pages = [t for t in br.trace if not t[1].startswith("/api/")]
    assert pages and all("enigma_rt" not in t[5] for t in pages), pages


# ---------------------------------------------------------------- failure paths must end at sign-in, with cookies cleared and no loop
def test_revoked_refresh_token_ends_at_signin_and_clears_cookies(br, stack):
    idp, _ = stack
    br.sign_in()
    idp.refresh_ok = False
    br.jar.pop("enigma_at")
    r = br.follow("/app/gpus")                                        # raises on a redirect loop
    assert br.trace[-1][1] == "/sign-in" and r["status"] == 200, br.trace[-5:]
    assert "enigma_rt" not in br.jar and "enigma_at" not in br.jar, "failed refresh must clear both cookies"


def test_anonymous_page_request_goes_to_signin_without_a_loop(br):
    r = br.request("GET", "/app/gpus")
    assert r["status"] in (302, 307)
    final = br.follow("/app/gpus")                                    # raises on a redirect loop
    assert br.trace[-1][1] == "/sign-in" and final["status"] == 200


@pytest.mark.parametrize("nxt", ["//evil.example/x", "https://evil.example", "/\\evil.example", "/app/../../etc", "/app%2f..%2f", "/api/auth/sign-out", "/sign-in", "javascript:alert(1)", "/app?x=1", "/app/a b", ""])
def test_refresh_endpoint_never_redirects_off_site_or_to_unlisted_paths(br, nxt):
    br.sign_in()
    br.jar.pop("enigma_at")
    from urllib.parse import quote
    r = br.request("GET", "/api/auth/refresh?next=" + quote(nxt, safe=""))
    assert r["status"] in (302, 307, 400, 404), r
    if r["location"]:
        loc = urlsplit(r["location"])
        assert loc.hostname in (None, "127.0.0.1") and loc.scheme in ("", "http")
        assert loc.path in ("/sign-in", "/app") or loc.path.startswith("/app/") or loc.path == "/onboarding", loc.path
        assert ".." not in loc.path and "//" not in loc.path and "\\" not in loc.path


# ---------------------------------------------------------------- unchanged behaviour: API refresh, CSRF, tenant identity
def test_bff_call_after_access_expiry_still_refreshes_via_api_path(br, stack):
    _, api = stack
    br.sign_in()
    br.jar.pop("enigma_at")
    r = br.request("GET", "/api/enigma/me")
    assert r["status"] == 200 and "enigma_at" in r["cookies"]
    assert api.auth_headers[-1].startswith("Bearer ey")


def test_state_changing_requests_still_require_same_origin(br):
    assert br.request("POST", "/api/auth/sign-in", {"email": "a@example.test", "password": "correct-horse-1"}, origin=False)["status"] == 403
    assert br.request("POST", "/api/auth/refresh", {})["status"] in (404, 405), "refresh must stay a cookie-bound GET, never a POST body API"
    br.sign_in()
    assert br.request("POST", "/api/enigma/workspaces", {"name": "x"}, origin=False)["status"] == 403


def test_api_without_any_cookie_is_401_json_not_a_redirect(br):
    r = br.request("GET", "/api/enigma/me")
    assert r["status"] == 401 and r["cache_control"] == "no-store" and r["location"] is None
