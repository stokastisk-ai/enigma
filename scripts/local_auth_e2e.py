"""Local end-to-end check of self-hosted Better Auth + FastAPI on a real PostgreSQL (LOCAL FIXTURE EVIDENCE ONLY, not production evidence).

Starts disposable containers (postgres:17, Mailpit as an SMTP sink with STARTTLS) plus the built Next.js app (`next start`) and the FastAPI app,
then drives the browser-facing routes over HTTP:
  sign-up -> verification code delivered by SMTP (read from Mailpit) -> verify (session cookie) -> BFF /api/enigma/me (server-minted EdDSA JWT,
  verified by FastAPI against the web app's JWKS) -> workspace create/list for TWO users -> cross-tenant read refused -> sign-out -> 401.
Also checks: unauthenticated BFF 401, cross-origin POST 403, wrong password 401, JWKS has Ed25519 keys only, auth role cannot read tenant tables,
API role cannot read auth tables. Prerequisites: docker, `npm run build` done in apps/web, Python deps of apps/api. Exit 0 only if every check passes.

    python3.13 scripts/local_auth_e2e.py
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import httpx
import psycopg

ROOT = Path(__file__).resolve().parent.parent
WEB, API = ROOT / "apps/web", ROOT / "apps/api"
PG_IMAGE, MAIL_IMAGE = "postgres:17", "axllent/mailpit:v1.29.6"
results: list[tuple[str, bool, str]] = []


def port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}", flush=True)


def wait(fn, what: str, timeout: float = 90) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return
        except Exception:                                  # noqa: BLE001  service not up yet
            pass
        time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for {what}")


def main() -> int:
    run_id = secrets.token_hex(4)
    pg_port, smtp_port, mail_http, web_port, api_port = (port() for _ in range(5))
    admin_pw, api_pw, auth_pw = (secrets.token_urlsafe(24) for _ in range(3))
    tmp = Path(tempfile.mkdtemp(prefix="enigma-auth-e2e-"))
    containers, procs = [f"enigma-e2e-pg-{run_id}", f"enigma-e2e-mail-{run_id}"], []
    try:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "1", "-subj", "/CN=localhost",
                        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1", "-keyout", str(tmp / "k.pem"), "-out", str(tmp / "c.pem")],
                       check=True, capture_output=True)
        os.chmod(tmp / "k.pem", 0o644)                     # read by the mailpit container user; throwaway key for a 1-day local cert
        subprocess.run(["docker", "run", "-d", "--rm", "--name", containers[0], "-e", f"POSTGRES_PASSWORD={admin_pw}", "-p", f"127.0.0.1:{pg_port}:5432", PG_IMAGE],
                       check=True, capture_output=True)
        subprocess.run(["docker", "run", "-d", "--rm", "--name", containers[1], "-v", f"{tmp}:/certs:ro", "-p", f"127.0.0.1:{smtp_port}:1025",
                        "-p", f"127.0.0.1:{mail_http}:8025", MAIL_IMAGE, "--smtp-tls-cert", "/certs/c.pem", "--smtp-tls-key", "/certs/k.pem",
                        "--smtp-require-starttls", "--smtp-auth-accept-any"], check=True, capture_output=True)
        admin = f"postgresql://postgres:{admin_pw}@127.0.0.1:{pg_port}/postgres"
        wait(lambda: psycopg.connect(admin, connect_timeout=2).close() or True, "postgres")
        sqldir = tmp / "sql"
        shutil.copytree(ROOT / "db/migrations", sqldir)
        env = dict(os.environ, ENIGMA_DB_ADMIN_DSN=admin, ENIGMA_DB_APP_PASSWORD=api_pw, ENIGMA_DB_AUTH_PASSWORD=auth_pw, ENIGMA_SQL_DIR=str(sqldir),
                   PYTHONPATH=str(API))
        b = subprocess.run([sys.executable, "-m", "enigma_api.bootstrap"], env=env, capture_output=True, text=True)
        check("bootstrap applies 0001-0004 and creates both least-privilege roles", b.returncode == 0 and "auth role is least-privilege" in b.stdout,
              b.stdout.strip().splitlines()[-1] if b.stdout.strip() else b.stderr[-200:])

        api_dsn = f"postgresql://enigma_api:{api_pw}@127.0.0.1:{pg_port}/postgres"
        auth_dsn = f"postgresql://enigma_web_auth:{auth_pw}@127.0.0.1:{pg_port}/postgres"
        with psycopg.connect(auth_dsn) as c:
            try:
                c.execute("select count(*) from public.workspaces")
                check("auth role cannot read tenant tables", False)
            except psycopg.errors.InsufficientPrivilege:
                check("auth role cannot read tenant tables", True)
        with psycopg.connect(api_dsn) as c:
            try:
                c.execute('select count(*) from auth."user"')
                check("API role cannot read auth tables", False)
            except psycopg.errors.InsufficientPrivilege:
                check("API role cannot read auth tables", True)

        origin = f"http://localhost:{web_port}"
        api_env = dict(os.environ, PYTHONPATH=str(API), ENIGMA_ENV="test", ENIGMA_STORE="postgres", ENIGMA_DB_DSN=api_dsn, ENIGMA_AUTH_PROVIDER="betterauth",
                       ENIGMA_AUTH_ISSUER=origin, ENIGMA_AUTH_JWKS_URL=f"{origin}/api/auth/jwks", CRON_SECRET=secrets.token_urlsafe(32))
        procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "enigma_api.main:create_app", "--factory", "--host", "127.0.0.1", "--port", str(api_port),
                                       "--no-access-log"], env=api_env, stdout=open(tmp / "api.log", "w"), stderr=subprocess.STDOUT))
        web_env = dict(os.environ, ENIGMA_AUTH="on", ENIGMA_PUBLIC_ORIGIN=origin, ENIGMA_PUBLIC_HOST=f"localhost:{web_port}", BETTER_AUTH_SECRET=secrets.token_urlsafe(48),
                       ENIGMA_AUTH_DB_DSN=auth_dsn, ENIGMA_SMTP_URL=f"smtp://e2e:e2e@localhost:{smtp_port}", ENIGMA_MAIL_FROM="enigma@localhost",
                       NODE_EXTRA_CA_CERTS=str(tmp / "c.pem"), ENIGMA_API_URL=f"http://127.0.0.1:{api_port}", ENIGMA_DEV_AUTH="", NODE_ENV="production",
                       ENIGMA_PUBLIC_SIGNUP="on")      # this harness creates accounts; the production default is closed (private beta)
        procs.append(subprocess.Popen(["npx", "next", "start", "-p", str(web_port), "-H", "127.0.0.1"], cwd=WEB, env=web_env,
                                      stdout=open(tmp / "web.log", "w"), stderr=subprocess.STDOUT))
        wait(lambda: httpx.get(f"http://127.0.0.1:{api_port}/api/v1/health", timeout=2).status_code == 200, "api")
        wait(lambda: httpx.get(f"{origin}/sign-in", timeout=5).status_code == 200, "web", 120)

        jwks = httpx.get(f"{origin}/api/auth/jwks", timeout=10).json()
        check("JWKS publishes Ed25519 public keys only", bool(jwks.get("keys")) and all(k.get("crv") == "Ed25519" and "d" not in k for k in jwks["keys"]))

        def mailbox_code(addr: str) -> str:
            found: dict[str, str] = {}

            def look() -> bool:
                msgs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{mail_http}/api/v1/search?query=to:{addr}", timeout=5).read())["messages"]
                if not msgs:
                    return False
                body = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{mail_http}/api/v1/message/{msgs[0]['ID']}", timeout=5).read())["Text"]
                m = re.search(r"\b(\d{6})\b", body)
                if m:
                    found["code"] = m.group(1)
                return bool(m)
            wait(look, f"verification e-mail for {addr}", 30)
            return found["code"]

        hdr = {"origin": origin, "content-type": "application/json"}
        users = []
        for i in (1, 2):
            email, pw = f"e2e{i}-{run_id}@example.test", f"Correct-Horse-{secrets.token_hex(6)}-Battery"
            c = httpx.Client(base_url=origin, timeout=30)
            r = c.post("/api/auth/sign-up", headers=hdr, json={"email": email, "password": pw})
            check(f"user{i} sign-up accepted, verification required", r.status_code == 200 and r.json().get("needsVerify") is True, str(r.status_code))
            r = c.post("/api/auth/sign-in", headers=hdr, json={"email": email, "password": pw})
            check(f"user{i} unverified sign-in refused with needsVerify", r.status_code == 403 and r.json().get("needsVerify") is True, str(r.status_code))
            code = mailbox_code(email)
            bad = "000000" if code != "000000" else "111111"
            r = c.post("/api/auth/verify", headers=hdr, json={"email": email, "code": bad})
            check(f"user{i} wrong code refused", r.status_code == 422, str(r.status_code))
            r = c.post("/api/auth/verify", headers=hdr, json={"email": email, "code": code})
            check(f"user{i} code from SMTP verifies and signs in", r.status_code == 200 and r.json().get("signedIn") is True and any(
                n.startswith("enigma-auth.") for n in c.cookies.keys()), str(r.status_code))
            cookie_flags = " ".join(r.headers.get_list("set-cookie")).lower()
            check(f"user{i} session cookie is HttpOnly + SameSite=Lax", "httponly" in cookie_flags and "samesite=lax" in cookie_flags)
            me = c.get("/api/enigma/me")
            ok = me.status_code == 200 and str(me.json().get("user_id", me.json().get("id", ""))).startswith("ba:")
            check(f"user{i} BFF -> FastAPI accepts the server-minted EdDSA token", ok, f"{me.status_code} {str(me.json())[:120]}")
            users.append((c, email, pw))

        (c1, e1, p1), (c2, _, _) = users
        r = c1.post("/api/enigma/workspaces", headers=hdr, json={"name": f"E2E One {run_id}", "slug": f"e2e-one-{run_id}"})
        check("user1 creates a workspace", r.status_code in (200, 201), f"{r.status_code} {r.text[:160]}")
        j = r.json() if r.status_code in (200, 201) else {}
        ws1 = (j.get("workspace") or {}).get("id") or j.get("id")
        l2 = c2.get("/api/enigma/workspaces")
        check("user2 cannot see user1's workspace", l2.status_code == 200 and bool(ws1) and ws1 not in json.dumps(l2.json()), f"{l2.status_code} ws1={'set' if ws1 else 'missing'}")
        if ws1:
            x = c2.get(f"/api/enigma/workspaces/{ws1}/projects")
            check("user2 cannot read user1's workspace by id", x.status_code in (403, 404), str(x.status_code))

        anon = httpx.get(f"{origin}/api/enigma/me", timeout=10)
        check("unauthenticated BFF call is 401", anon.status_code == 401, str(anon.status_code))
        r = httpx.post(f"{origin}/api/auth/sign-in", headers={"origin": "https://evil.example.test", "content-type": "application/json"},
                       json={"email": e1, "password": p1}, timeout=10)
        check("cross-origin sign-in POST is 403", r.status_code == 403, str(r.status_code))
        r = httpx.post(f"{origin}/api/auth/sign-in", headers=hdr, json={"email": e1, "password": p1 + "x"}, timeout=10)
        check("wrong password is a generic 401", r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials", str(r.status_code))
        r = c1.post("/api/auth/sign-out", headers=hdr, json={})
        after = c1.get("/api/enigma/me")
        check("sign-out ends the session (BFF 401 afterwards)", r.status_code == 200 and after.status_code == 401, f"{r.status_code}/{after.status_code}")
        c3 = httpx.Client(base_url=origin, timeout=30)
        r = c3.post("/api/auth/sign-in", headers=hdr, json={"email": e1, "password": p1})
        check("verified user signs in again with the password", r.status_code == 200 and c3.get("/api/enigma/me").status_code == 200, str(r.status_code))
    except Exception as e:                                                   # noqa: BLE001
        check("harness completed", False, f"{type(e).__name__}: {str(e)[:300]}")
        for f in ("api.log", "web.log"):
            if (tmp / f).exists():
                print(f"--- tail {f}\n" + "\n".join((tmp / f).read_text().splitlines()[-25:]))
    finally:
        for p in procs:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        for name in containers:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 0 if results and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
