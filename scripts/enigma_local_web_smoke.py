#!/usr/bin/env python3
"""Python 3.13, loopback-only Enigma Next.js route smoke check (v2, 2026-11-23).

    python3.13 scripts/enigma_local_web_smoke.py                 # check a server that is already running on 127.0.0.1:3000
    python3.13 scripts/enigma_local_web_smoke.py --start         # launch `npm run dev` from apps/web, check it, stop only what it launched

Works from any directory: apps/web is discovered from --project (repo root or apps/web), the current directory, or this script's own location.
Always talks to 127.0.0.1 over plain sockets (no proxies, no redirects followed). It never uses AWS, tunnels or any GPU, and it terminates only the
process group it created. A machine-readable report is written to <repo>/.enigma-local/smoke-report-<UTC>.json.

Result classes per route: ok | gated (redirect/401/403/404 on a protected route: expected) | fail_closed (503 "auth not configured": expected when
unconfigured) | fail (timeout, reset, 5xx other than 503, wrong status for a public page). Exit codes: 0 all routes ok/gated/fail_closed;
1 at least one route failed; 2 usage/config error; 3 environment problem (port occupied, server did not start, unrelated service).
"""
from __future__ import annotations

import argparse
import datetime as dt
import http.client
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HOST = "127.0.0.1"
# route -> kind. "public" pages must answer 200; "protected" pages/APIs may be auth-gated; "unlisted" paths are NOT in the BFF allow-list
# (apps/web/app/api/enigma/[...path]/route.ts answers 404 for anything unlisted, by design) and must never be served.
ROUTES: dict[str, str] = {
    "/": "public", "/sign-in": "public", "/sign-up": "public",
    "/app": "protected", "/app/gpus": "protected", "/app/inference": "protected", "/app/playground": "protected",
    "/api/enigma/me": "protected",
    "/api/enigma/health": "unlisted",
}
REDIRECTS = {301, 302, 303, 307, 308}
GATED = REDIRECTS | {401, 403, 404}
STRIP_ENV_PREFIXES = ("AWS_", "CUDA_", "NVIDIA_")
STRIP_ENV = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"}

_SECRETS = [
    (re.compile(r"\b(?:sk|pk|rk)_(?:test|live)_[A-Za-z0-9_\-=]{6,}"), "<clerk-key>"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]*"), "<jwt>"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "<aws-key-id>"),
    (re.compile(r"arn:aws[a-z\-]*:[^\s\"']+"), "<arn>"),
    (re.compile(r"(?i)\b(authorization|cookie|set-cookie)\b\s*[:=]\s*[^\r\n]*"), r"\1: <redacted>"),
    (re.compile(r"(?i)\b([A-Za-z0-9_]*(?:secret|token|password|passwd|api[_-]?key|credential)[A-Za-z0-9_]*)\b\s*[=:]\s*[^\s\"',;]+"), r"\1=<redacted>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
]


def redact(text: str) -> str:
    for rx, rep in _SECRETS:
        text = rx.sub(rep, text)
    return text


# ----------------------------------------------------------------------------------------------------------------------------- discovery
def find_web_dir(project: Path | None = None, cwd: Path | None = None, script: Path | None = None) -> Path:
    """Return apps/web. `project` may be the repo root or apps/web itself; otherwise search upward from cwd, then from this script."""
    def probe(base: Path) -> Path | None:
        base = base.resolve()
        for cand in (base, *base.parents):
            if (cand / "apps" / "web" / "package.json").is_file():
                return cand / "apps" / "web"
            if cand.name == "web" and cand.parent.name == "apps" and (cand / "package.json").is_file():
                return cand
        return None
    starts = [project] if project else [cwd or Path.cwd(), script or Path(__file__).parent]
    for s in starts:
        found = probe(s)
        if found:
            return found
    where = ", ".join(str(s) for s in starts)
    raise FileNotFoundError(f"apps/web/package.json not found from: {where}. The Next.js package is apps/web (there is no package.json at the repository root).")


def repo_root(web: Path) -> Path:
    return web.parents[1]


# ----------------------------------------------------------------------------------------------------------------------------- probing
@dataclass
class Probe:
    outcome: str                      # HTTP | NO_SERVER | TIMEOUT | RESET | ERROR
    status: int | None = None
    location_path: str | None = None  # path only: redirect queries can carry sensitive values
    title: str | None = None
    seconds: float = 0.0
    error: str | None = None
    server_header: str | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def http_probe(port: int, path: str, timeout: float, host_header: str | None = None) -> Probe:
    t0 = time.monotonic()
    conn = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"host": host_header or f"{HOST}:{port}", "user-agent": "EnigmaLocalSmoke/2.0", "accept": "*/*", "connection": "close"})
        r = conn.getresponse()
        body = r.read(65536)
        loc = r.getheader("location")
        title = re.search(rb"<title[^>]*>([^<]{0,200})</title>", body, re.I)
        return Probe("HTTP", r.status, location_path=(re.sub(r"^[a-z]+://[^/]+", "", loc).split("?")[0] if loc else None),
                     title=title.group(1).decode("utf-8", "replace") if title else None, seconds=round(time.monotonic() - t0, 2), server_header=r.getheader("x-powered-by"))
    except ConnectionRefusedError:
        return Probe("NO_SERVER", seconds=round(time.monotonic() - t0, 2), error="connection refused")
    except (TimeoutError, socket.timeout):
        # connect succeeded (else we would have been refused) but no response arrived: a server is there and it is stuck
        return Probe("TIMEOUT", seconds=round(time.monotonic() - t0, 2), error=f"no response within {timeout:g}s")
    except (ConnectionResetError, http.client.RemoteDisconnected, BrokenPipeError) as ex:
        return Probe("RESET", seconds=round(time.monotonic() - t0, 2), error=type(ex).__name__)
    except OSError as ex:
        return Probe("ERROR", seconds=round(time.monotonic() - t0, 2), error=type(ex).__name__)
    finally:
        conn.close()


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((HOST, port)) == 0


def classify(kind: str, p: Probe) -> tuple[str, str]:
    """Return (class, reason) for one route according to the route contract."""
    if p.outcome == "NO_SERVER":
        return "fail", "no server is listening"
    if p.outcome == "TIMEOUT":
        return "fail", "server accepted the connection but never answered (hung request)"
    if p.outcome == "RESET":
        return "fail", "connection reset by the server (ECONNRESET / socket hang up)"
    if p.outcome != "HTTP" or p.status is None:
        return "fail", p.error or "unexpected transport error"
    s = p.status
    if kind == "unlisted":
        if s == 200:
            return "fail", "an unlisted BFF path was served (the allow-list must answer 404)"
        if s == 404:
            return "gated", "404: not in the BFF allow-list (the contract for unlisted paths)"
        return ("gated", "blocked by the authentication gate before routing") if s in GATED or s == 503 else ("fail", f"unexpected status {s}")
    if kind == "public":
        return ("ok", "public page served") if s == 200 else ("fail", f"public page returned {s}")
    if s == 200:
        return "ok", "served (a session is present or dev auth is active)"
    if s in GATED:
        return "gated", "authentication gate (expected for an anonymous request)"
    if s == 503:
        return "fail_closed", "503: authentication is not configured, so the route is refused (fail closed)"
    if s == 502:
        return "fail", "502: upstream API unreachable from the BFF"
    return "fail", f"unexpected status {s}"


def is_enigma(p: Probe) -> bool:
    return p.outcome == "HTTP" and p.status == 200 and bool(p.title and "enigma" in p.title.lower())


# ----------------------------------------------------------------------------------------------------------------------------- launching
EXIT_HINTS = [
    (re.compile(r"EADDRINUSE|address already in use", re.I), "PORT_OCCUPIED", "another process took the port while starting"),
    (re.compile(r"Cannot find module|MODULE_NOT_FOUND|next: not found|sh: 1: next", re.I), "DEPENDENCIES_MISSING", "run `npm ci` in apps/web"),
    (re.compile(r"ENOENT.*package\.json|Missing script", re.I), "WRONG_DIRECTORY_OR_SCRIPT", "npm did not find a package.json/script in the working directory"),
    (re.compile(r"Error: .*(config|next\.config)|Failed to load", re.I), "CONFIG_ERROR", "next.config/proxy failed to load"),
    (re.compile(r"ECONNRESET|socket hang up|Failed to proxy", re.I), "SELF_PROXY_LOOP", "Next proxied a request to itself (see apps/web/lib/server/loopback-rewrite.ts)"),
]


def classify_exit(code: int | None, log: str) -> tuple[str, str]:
    for rx, name, hint in EXIT_HINTS:
        if rx.search(log):
            return name, hint
    return "EXITED_EARLY", f"process exited with code {code}; see the redacted log tail"


@dataclass
class Launched:
    proc: subprocess.Popen
    log_path: Path
    log_file: object = field(repr=False, default=None)

    def log_text(self) -> str:
        try:
            return redact(self.log_path.read_text(errors="replace"))
        except OSError:
            return ""

    def stop(self) -> dict:
        info = {"signalled": False, "exit_code": self.proc.poll()}
        if self.proc.poll() is None or _group_alive(self.proc.pid):
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(self.proc.pid, sig)      # the group this runner created (start_new_session), never anyone else's
                    info["signalled"] = True
                except ProcessLookupError:
                    break
                try:
                    self.proc.wait(timeout=8)
                    break
                except subprocess.TimeoutExpired:
                    continue
        info["exit_code"] = self.proc.poll()
        if self.log_file:
            self.log_file.close()
        return info


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def child_env(unconfigured: bool, dev_auth: bool = False, api_url: str = "http://127.0.0.1:8787") -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV and not k.startswith(STRIP_ENV_PREFIXES)}
    env.update({"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost", "NEXT_TELEMETRY_DISABLED": "1", "CLERK_TELEMETRY_DISABLED": "1"})
    blank = {"CLERK_SECRET_KEY": "", "CLERK_PUBLISHABLE_KEY": "", "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY": "", "ENIGMA_AUTH_PROVIDER": ""}
    if unconfigured:                                    # empty process env wins over .env.local: simulates "no auth provider configured"
        env.update({**blank, "ENIGMA_DEV_AUTH": ""})
    elif dev_auth:                                      # the guarded dev login (local mock stack only); never combined with a real provider
        secret = os.environ.get("ENIGMA_DEV_JWT_SECRET", "")
        if len(secret) < 32:
            secret = secrets.token_hex(32)              # throwaway; share it with a local API via ENIGMA_DEV_JWT_SECRET to get end-to-end 200s
        env.update({**blank, "ENIGMA_DEV_AUTH": "1", "ENIGMA_DEV_JWT_SECRET": secret, "ENIGMA_API_URL": api_url})
    return env


def launch(web: Path, port: int, command: list[str] | None, unconfigured: bool, log_dir: Path, dev_auth: bool = False, api_url: str = "http://127.0.0.1:8787") -> Launched:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"smoke-next-{port}.log"
    f = open(log_path, "wb")
    cmd = command or ["npm", "run", "dev", "--", "--hostname", HOST, "--port", str(port)]
    proc = subprocess.Popen(cmd, cwd=web, env=child_env(unconfigured, dev_auth, api_url), stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    return Launched(proc, log_path, f)


# ----------------------------------------------------------------------------------------------------------------------------- orchestration
def run(args: argparse.Namespace) -> tuple[int, dict]:
    report: dict = {"tool": "enigma_local_web_smoke", "version": 2, "utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "host": HOST, "port": args.port}
    try:
        web = find_web_dir(args.project)
    except FileNotFoundError as ex:
        report.update(result="USAGE_ERROR", error=str(ex))
        return 2, report
    report["web_dir"] = str(web)
    launched: Launched | None = None
    try:
        busy = port_in_use(args.port)
        if args.start:
            if busy:
                ident = http_probe(args.port, "/sign-in", args.request_timeout)
                report.update(result="PORT_OCCUPIED", occupant={"identity": "enigma" if is_enigma(ident) else "unknown", **ident.as_dict()},
                              advice="Stop that server yourself or pick --port N. Nothing was started and nothing was killed.")
                return 3, report
            launched = launch(web, args.port, args.command, args.unconfigured, repo_root(web) / ".enigma-local", args.dev_auth, args.api_url)
            deadline = time.monotonic() + args.startup_seconds
            while True:
                if launched.proc.poll() is not None:
                    name, hint = classify_exit(launched.proc.returncode, launched.log_text())
                    report.update(result="START_FAILED", classification=name, hint=hint, exit_code=launched.proc.returncode, log_tail=launched.log_text()[-3000:])
                    return 3, report
                if port_in_use(args.port):
                    break
                if time.monotonic() > deadline:
                    report.update(result="START_TIMEOUT", hint=f"nothing listened on {HOST}:{args.port} within {args.startup_seconds}s", log_tail=launched.log_text()[-3000:])
                    return 3, report
                time.sleep(0.4)
        elif not busy:
            report.update(result="NO_SERVER", hint=f"nothing is listening on {HOST}:{args.port}; start it with --start or `npm run dev -- --hostname {HOST} --port {args.port}` in apps/web")
            return 3, report

        ident = http_probe(args.port, "/sign-in", args.warmup_timeout)    # first request compiles the route: allow longer
        report["identity_probe"] = ident.as_dict()
        if ident.outcome != "HTTP":
            cls, why = classify("public", ident)
            report.update(result="SERVER_UNRESPONSIVE", hint=why, log_tail=(launched.log_text()[-3000:] if launched else None))
            return 1, report
        if not is_enigma(ident) and not args.allow_unverified:
            report.update(result="UNRELATED_SERVICE", hint="the listener on this port did not identify itself as Enigma (title lacks 'Enigma'); refusing to test it", identity="unknown")
            return 3, report
        report["identity"] = "enigma" if is_enigma(ident) else "unverified"
        routes = {}
        for path, kind in ROUTES.items():
            p = http_probe(args.port, path, args.request_timeout)
            cls, why = classify(kind, p)
            routes[path] = {"kind": kind, "class": cls, "reason": why, **p.as_dict()}
        report["routes"] = routes
        failed = [r for r, v in routes.items() if v["class"] == "fail"]
        report["result"] = "FAIL" if failed else "PASS"
        report["failed_routes"] = failed
        if launched:
            tail = launched.log_text()
            report["server_log_flags"] = {"failed_to_proxy": "Failed to proxy" in tail, "econnreset": "ECONNRESET" in tail}
            if report["server_log_flags"]["failed_to_proxy"]:
                report["result"], report["failed_routes"] = "FAIL", failed or ["(server log shows 'Failed to proxy')"]
        return (1 if report["result"] == "FAIL" else 0), report
    finally:
        if launched:
            report["stopped"] = launched.stop()
            report["port_free_after"] = not port_in_use(args.port)


def write_report(report: dict, web: Path | None) -> Path | None:
    base = repo_root(web) if web else Path.cwd()
    out = base / ".enigma-local"
    try:
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"smoke-report-{report['utc'].replace(':', '')}.json"
        path.write_text(json.dumps(report, indent=2) + "\n")
        return path
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", type=Path, default=None, help="repository root or apps/web (default: discovered from the current directory / this script)")
    p.add_argument("--port", type=int, default=3000)
    p.add_argument("--start", action="store_true", help="launch `npm run dev` on 127.0.0.1 from apps/web and stop it afterwards")
    p.add_argument("--unconfigured", action="store_true", help="with --start: blank the auth provider variables to exercise the fail-closed (503) behaviour")
    p.add_argument("--dev-auth", action="store_true", help="with --start: use the guarded dev login (Clerk blanked) so protected routes answer 200; run a local API on --api-url")
    p.add_argument("--api-url", default="http://127.0.0.1:8787", help="loopback FastAPI base URL handed to the BFF in --dev-auth mode")
    p.add_argument("--startup-seconds", type=float, default=90.0)
    p.add_argument("--warmup-timeout", type=float, default=60.0, help="first request (route compilation)")
    p.add_argument("--request-timeout", type=float, default=30.0)
    p.add_argument("--allow-unverified", action="store_true", help="test a listener even if it does not identify itself as Enigma")
    p.add_argument("--command-json", default=None, help=argparse.SUPPRESS)   # tests: replace the launch command with a JSON list
    p.add_argument("--no-report", action="store_true")
    a = p.parse_args(argv)
    a.command = json.loads(a.command_json) if a.command_json else None
    if a.dev_auth and a.unconfigured:
        p.error("--dev-auth and --unconfigured are mutually exclusive")
    if not a.api_url.startswith(("http://127.0.0.1:", "http://localhost:")):
        p.error("--api-url must be a loopback address")
    if not 1024 <= a.port <= 65535:
        p.error("use an unprivileged local port 1024..65535")
    code, report = run(a)
    web = Path(report["web_dir"]) if report.get("web_dir") else None
    path = None if a.no_report else write_report(report, web)
    print(json.dumps({k: v for k, v in report.items() if k not in ("log_tail",)}, indent=2))
    if report.get("log_tail"):
        print("--- redacted server log tail ---\n" + report["log_tail"])
    if path:
        print(f"report: {path}")
    print("Interpretation: ok/gated/fail_closed are expected outcomes; only 'fail' is a defect. This checks HTTP routing, not real Cognito/Clerk/AWS/Kaggle.")
    return code


if __name__ == "__main__":
    sys.exit(main())
