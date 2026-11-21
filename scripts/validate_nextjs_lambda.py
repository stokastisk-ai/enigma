"""Local, CPU-only validation of the Next.js 16 standalone package intended for AWS Lambda (Lambda Web Adapter). NO AWS, NO GPU, NO real Cognito.

    /usr/local/bin/python3.13 scripts/validate_nextjs_lambda.py [--build] [--json]

What it checks (everything a laptop can check):
  * PACKAGE: server.js present, ZERO native binaries (.node / .so => architecture independent, so ARM64 is safe), size, no .env files, and the
    local ignored apps/web/.env.local secrets are NOT present anywhere in the package (boolean only, values never printed).
  * BEHAVIOUR behind a CloudFront-style reverse proxy that (like CloudFront -> Function URL) REPLACES the viewer Host with a Function-URL host:
    SSR, immutable static assets, redirects staying on the public origin, BFF 401, CSRF same-origin rules incl. X-Forwarded-Host spoofing,
    multiple Set-Cookie headers (HttpOnly, Secure, SameSite) surviving the proxy, response chunking, resident memory.
What it can NOT prove: the real Lambda Web Adapter (needs the Lambda runtime), API Gateway v2 payload translation, real CloudFront behaviour,
cold-start latency on Lambda, the 6 MB buffered-response limit in practice. Those stay NOT RUN until an approved deployment.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
DIST = ".next-standalone"
PKG = WEB / DIST / "standalone"
PUBLIC_HOST = "d111abcdef.cloudfront.net"
FUNCTION_URL_HOST = "abc123def456.lambda-url.us-east-1.on.aws"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade", "host", "content-length", "transfer-encoding"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- package
def build() -> None:
    env = {**os.environ, "ENIGMA_STANDALONE": "1", "ENIGMA_DIST_DIR": DIST}
    subprocess.run(["npx", "next", "build"], cwd=WEB, env=env, check=True, capture_output=True)


def assemble() -> None:
    """What a Lambda packaging step must do: Next's standalone output omits static assets and public/."""
    static_src, static_dst = WEB / DIST / "static", PKG / DIST / "static"
    if static_src.exists():
        shutil.copytree(static_src, static_dst, dirs_exist_ok=True)
    if (WEB / "public").exists():
        shutil.copytree(WEB / "public", PKG / "public", dirs_exist_ok=True)


def local_secret_values() -> list[str]:
    f = WEB / ".env.local"
    if not f.exists():
        return []
    vals = []
    for line in f.read_text().splitlines():
        m = re.match(r"^(?:CLERK_SECRET_KEY|[A-Z_]*SECRET[A-Z_]*|[A-Z_]*TOKEN[A-Z_]*|[A-Z_]*PASSWORD[A-Z_]*)=(.+)$", line.strip())
        if m and len(m.group(1).strip("'\"")) >= 12:
            vals.append(m.group(1).strip("'\""))
    return vals


def audit_package() -> dict:
    files = [p for p in PKG.rglob("*") if p.is_file()]
    natives = [str(p.relative_to(PKG)) for p in files if p.suffix in (".node", ".so") or ".so." in p.name or p.suffix == ".dylib"]
    envs = [str(p.relative_to(PKG)) for p in files if p.name.startswith(".env")]
    secrets = local_secret_values()
    leaked = False
    if secrets:
        for p in files:
            if p.stat().st_size > 5_000_000:
                continue
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if any(s.encode() in data for s in secrets):
                leaked = True
                break
    return {"server_js": (PKG / "server.js").exists(), "size_mb": round(sum(p.stat().st_size for p in files) / 1e6, 1), "file_count": len(files),
            "native_binaries": natives, "env_files": envs, "local_secrets_checked": len(secrets), "local_secret_found_in_package": leaked}


# ---------------------------------------------------------------- CloudFront-like proxy (viewer Host is replaced, as with a Function URL origin)
class Proxy(BaseHTTPRequestHandler):
    origin_port = 0
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # noqa: D401
        pass

    def _go(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else None
        h = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        h.update({"Host": FUNCTION_URL_HOST, "X-Amz-Cf-Id": "local-test", "CloudFront-Forwarded-Proto": "https", "Via": "1.1 local.cloudfront.net (CloudFront)"})
        c = http.client.HTTPConnection("127.0.0.1", self.origin_port, timeout=30)
        c.request(self.command, self.path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        self.send_response(r.status)
        for k, v in r.getheaders():                                   # getheaders() keeps duplicates: every Set-Cookie survives
            if k.lower() not in HOP:
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Origin-Transfer-Encoding", r.getheader("transfer-encoding") or "none")
        self.end_headers()
        self.wfile.write(data)
        c.close()

    do_GET = do_POST = do_PUT = do_DELETE = _go


def request(port: int, method: str, path: str, headers: dict | None = None, body: bytes | None = None) -> tuple[int, list[tuple[str, str]], bytes]:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request(method, path, body=body, headers={"Host": PUBLIC_HOST, **(headers or {})})
    r = c.getresponse()
    data = r.read()
    out = (r.status, r.getheaders(), data)
    c.close()
    return out


def hdr(headers: list[tuple[str, str]], name: str) -> list[str]:
    return [v for k, v in headers if k.lower() == name.lower()]


# ---------------------------------------------------------------- run
def run(do_build: bool) -> dict:
    if do_build or not (PKG / "server.js").exists():
        build()
    assemble()
    results: list[dict] = []

    def check(name: str, ok: bool, detail: str = "", info: bool = False) -> None:
        results.append({"check": name, "result": ("INFO" if info else "PASS" if ok else "FAIL"), "detail": detail})

    pkg = audit_package()
    check("package has server.js", pkg["server_js"])
    check("package has ZERO native binaries (architecture independent: ARM64-safe)", not pkg["native_binaries"], str(pkg["native_binaries"][:3]))
    check("package contains no .env files", not pkg["env_files"], str(pkg["env_files"]))
    check("local ignored secrets are not in the package", not pkg["local_secret_found_in_package"], f"{pkg['local_secrets_checked']} secret value(s) checked, none printed")
    check("package size is modest", pkg["size_mb"] < 60, f"{pkg['size_mb']} MB, {pkg['file_count']} files (Lambda zip limit 250 MB unzipped)")

    origin_port, proxy_port = free_port(), free_port()
    env = {**os.environ, "PORT": str(origin_port), "HOSTNAME": "127.0.0.1", "NODE_ENV": "production", "ENIGMA_AUTH_PROVIDER": "cognito",
           "NEXT_PUBLIC_COGNITO_REGION": "us-east-1", "NEXT_PUBLIC_COGNITO_USER_POOL_ID": "us-east-1_TESTPOOL", "NEXT_PUBLIC_COGNITO_CLIENT_ID": "testclientid123",
           "ENIGMA_API_URL": "http://127.0.0.1:1", "ENIGMA_PUBLIC_HOST": PUBLIC_HOST, "ENIGMA_PUBLIC_ORIGIN": f"https://{PUBLIC_HOST}", "ENIGMA_FORBID_LOCAL_GPU": "1"}
    for k in ("CLERK_SECRET_KEY", "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "ENIGMA_DEV_AUTH"):
        env.pop(k, None)
    t0 = time.time()
    node = subprocess.Popen(["node", "server.js"], cwd=PKG, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    srv = None
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", origin_port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        cold_ms = int((time.time() - t0) * 1000)
        Proxy.origin_port = origin_port
        srv = ThreadingHTTPServer(("127.0.0.1", proxy_port), Proxy)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        P = proxy_port
        sign_out = "/api/auth/sign-out"
        J = {"content-type": "application/json"}

        st, h, body = request(P, "GET", "/")
        check("SSR: GET / -> 200 HTML", st == 200 and b"Enigma" in body and "text/html" in (hdr(h, "content-type") or [""])[0], f"{st}, {len(body)} bytes")
        check("security headers present, x-powered-by absent", bool(hdr(h, "x-frame-options")) and bool(hdr(h, "x-content-type-options")) and not hdr(h, "x-powered-by"))
        check("origin response framing observed (informational)", True, f"transfer-encoding={hdr(h, 'x-origin-transfer-encoding')}", info=True)
        asset = re.search(rb'/_next/static/[^"\']+\.(?:js|css)', body)
        if asset:
            st2, h2, b2 = request(P, "GET", asset.group(0).decode())
            cc = (hdr(h2, "cache-control") or [""])[0]
            check("static asset served and immutable (cacheable by CloudFront)", st2 == 200 and "immutable" in cc and "max-age=31536000" in cc, f"{st2} {cc}")
        else:
            check("static asset reference found in HTML", False, "no /_next/static URL in the page")
        st, h, _ = request(P, "GET", "/sign-in")
        check("SSR: /sign-in -> 200", st == 200)
        st, h, _ = request(P, "GET", "/app")
        loc = (hdr(h, "location") or [""])[0]
        check("unauthenticated page redirect stays on the PUBLIC origin (never the Function URL host)", st in (307, 308) and loc.startswith(f"https://{PUBLIC_HOST}/") and FUNCTION_URL_HOST not in loc, f"{st} {loc}")
        check("the redirect is not cacheable by a CDN", "no-store" in (hdr(h, "cache-control") or [""])[0])
        st, h, body = request(P, "GET", "/api/enigma/workspaces")
        check("BFF without a session -> 401, not cacheable", st == 401 and "no-store" in (hdr(h, "cache-control") or [""])[0], f"{st}")
        st, _, _ = request(P, "POST", sign_out, {**J, "Origin": "https://evil.example"}, b"{}")
        check("CSRF: cross-origin POST refused (403)", st == 403)
        st, _, _ = request(P, "POST", sign_out, J, b"{}")
        check("CSRF: POST without Origin refused (403)", st == 403)
        st, _, _ = request(P, "POST", sign_out, {**J, "Origin": "https://evil.example", "X-Forwarded-Host": "evil.example"}, b"{}")
        check("CSRF: spoofed X-Forwarded-Host does not help (403)", st == 403)
        st, _, _ = request(P, "POST", sign_out, {**J, "Origin": "https://evil.example", "X-Forwarded-Host": PUBLIC_HOST}, b"{}")
        check("CSRF: spoofing the REAL public host in X-Forwarded-Host does not help (403)", st == 403)
        st, _, _ = request(P, "POST", sign_out, {**J, "Origin": f"https://{FUNCTION_URL_HOST}"}, b"{}")
        check("origin matching the (rewritten) Function-URL Host is the app's own origin (200)", st == 200, "same-origin by definition; CloudFront should be the only public entry")
        st, h, _ = request(P, "POST", sign_out, {**J, "Origin": f"https://{PUBLIC_HOST}"}, b"{}")
        cookies = hdr(h, "set-cookie")
        check("legit CloudFront-origin POST accepted (200)", st == 200, f"{st}")
        check("two Set-Cookie headers survive the proxy", len(cookies) == 2, f"{len(cookies)} cookies")
        flat = " | ".join(cookies).lower()
        check("cookies are HttpOnly, Secure, SameSite=Lax and cleared (Max-Age=0)", all(x in flat for x in ("httponly", "secure", "samesite=lax", "max-age=0")), "")
        check("refresh cookie is path-scoped to /api", "path=/api" in flat)
        st, _, _ = request(P, "POST", "/api/auth/sign-in", {**J, "Origin": f"https://{PUBLIC_HOST}"}, b'{"email":"nope","password":"x"}')
        check("input validation before any IdP call (422)", st == 422)
        st, _, _ = request(P, "GET", "/api/enigma/admin/aws/status")
        check("admin AWS route without a session -> 401 (default deny)", st == 401)
        for m in ("PUT", "DELETE"):
            st, _, _ = request(P, m, "/api/enigma/admin/aws/status", J, b"{}")
            check(f"BFF {m} never succeeds (401 before routing, or 404/405)", st in (401, 404, 405), f"{st}")
        rss = None
        try:
            for line in Path(f"/proc/{node.pid}/status").read_text().splitlines():
                if line.startswith("VmRSS"):
                    rss = round(int(line.split()[1]) / 1024)
        except OSError:
            pass
        check("resident memory of the Node process after the checks (informational: sizes the Lambda memory setting)", True, f"{rss} MB RSS; local cold start to first listen {cold_ms} ms (NOT a Lambda cold start)", info=True)
    finally:
        if srv:
            srv.shutdown()
        node.terminate()
        try:
            node.wait(timeout=5)
        except subprocess.TimeoutExpired:
            node.kill()
    fails = [r for r in results if r["result"] == "FAIL"]
    return {"package": pkg, "node": subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip(), "results": results, "failures": len(fails),
            "not_proven": ["real Lambda Web Adapter", "API Gateway v2 / Function URL payload translation", "real CloudFront", "Lambda cold start", "6 MB response limit in practice"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = run(a.build)
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"node {out['node']} | package {out['package']['size_mb']} MB, {len(out['package']['native_binaries'])} native binaries")
        for r in out["results"]:
            print(f"  [{r['result']}] {r['check']}" + (f"  ({r['detail']})" if r["detail"] else ""))
        print(f"FAILURES: {out['failures']}   NOT PROVEN locally: {', '.join(out['not_proven'])}")
    return 1 if out["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
