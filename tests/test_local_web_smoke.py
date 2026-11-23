"""scripts/enigma_local_web_smoke.py (v2): discovery, port ownership, failure classification, redaction, process hygiene.

Everything is loopback-only and uses a stub HTTP server started through the runner's own launch hook; no Next.js, Node, AWS or GPU is involved.
"""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "enigma_local_web_smoke.py"
spec = importlib.util.spec_from_file_location("enigma_local_web_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
sys.modules["enigma_local_web_smoke"] = smoke
spec.loader.exec_module(smoke)

STUB = textwrap.dedent(
    '''
    import os, socket, sys, time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    mode, port = sys.argv[1], int(sys.argv[2])
    open(os.environ.get("STUB_CWD_FILE", os.devnull), "w").write(os.getcwd())
    if mode == "exit3":
        print("boot failed: CLERK_SECRET_KEY=sk_test_SUPERSECRETVALUE123456 token=abc123def456", file=sys.stderr); sys.exit(3)
    if mode == "eaddrinuse":
        print("Error: listen EADDRINUSE: address already in use 127.0.0.1:%d" % port, file=sys.stderr); sys.exit(1)
    if mode in ("hang", "reset"):
        s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", port)); s.listen(16)
        while True:
            c, _ = s.accept()
            if mode == "reset":
                c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00"); c.close()
            else:
                time.sleep(3600)
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            p = self.path.split("?")[0]
            title = "Other service" if mode == "unrelated" else "Enigma - GPU inference console"
            if p in ("/", "/sign-in", "/sign-up"):
                self.send_response(200); body = ("<html><title>%s</title></html>" % title).encode()
            elif mode == "unconfigured":
                self.send_response(503); body = b"Authentication is not configured"
            elif p.startswith("/api/"):
                self.send_response(401); body = b"{}"
            else:
                self.send_response(307); self.send_header("location", "/sign-in?redirect_url=%2Fsecret-query"); body = b""
            self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
    '''
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def repo(tmp_path):
    web = tmp_path / "repo" / "apps" / "web"
    web.mkdir(parents=True)
    (web / "package.json").write_text('{"name":"web"}')
    (tmp_path / "stub.py").write_text(STUB)
    return tmp_path / "repo"


def run_smoke(repo: Path, mode: str, port: int, *extra: str, cwd: Path | None = None, env: dict | None = None):
    cmd = [sys.executable, str(SCRIPT), "--project", str(repo), "--port", str(port), "--start", "--startup-seconds", "20", "--warmup-timeout", "4", "--request-timeout", "2",
           "--command-json", json.dumps([sys.executable, str(repo.parent / "stub.py"), mode, str(port)]), *extra]
    r = subprocess.run(cmd, cwd=cwd or repo.parent, capture_output=True, text=True, timeout=90, env={**os.environ, **(env or {})})
    out = r.stdout
    start = out.index("{")
    end = out.rindex("\n}") + 2
    return r.returncode, json.loads(out[start:end]), out


# --------------------------------------------------------------------------------------------------------- discovery / working directory
def test_discovery_from_repo_root_subdir_web_and_script(repo):
    web = repo / "apps" / "web"
    (repo / "apps" / "web" / "lib").mkdir()
    assert smoke.find_web_dir(repo) == web.resolve()                                    # --project = repo root
    assert smoke.find_web_dir(web) == web.resolve()                                     # --project = apps/web itself
    assert smoke.find_web_dir(None, cwd=repo) == web.resolve()                          # run from the repository root
    assert smoke.find_web_dir(None, cwd=web / "lib") == web.resolve()                   # run from a nested directory
    assert smoke.find_web_dir(None, cwd=repo.parent.parent, script=repo / "scripts") == web.resolve()   # falls back to the script's own location


def test_wrong_directory_is_reported_clearly_and_exits_2(tmp_path):
    (tmp_path / "empty").mkdir()
    r = subprocess.run([sys.executable, str(SCRIPT), "--project", str(tmp_path / "empty"), "--no-report"], capture_output=True, text=True, timeout=30, cwd=tmp_path / "empty")
    assert r.returncode == 2
    assert "apps/web/package.json not found" in r.stdout and "no package.json at the repository root" in r.stdout


def test_server_is_launched_with_cwd_apps_web_even_when_run_from_elsewhere(repo, tmp_path):
    elsewhere = tmp_path / "somewhere_else"
    elsewhere.mkdir()
    cwd_file = tmp_path / "cwd.txt"
    port = free_port()
    code, rep, _ = run_smoke(repo, "enigma", port, cwd=elsewhere, env={"STUB_CWD_FILE": str(cwd_file)})
    assert code == 0, rep
    assert Path(cwd_file.read_text()).resolve() == (repo / "apps" / "web").resolve(), "npm must be launched from apps/web, not the invocation directory"


# --------------------------------------------------------------------------------------------------------- outcomes
def test_success_redirect_and_fail_closed_are_distinguished_from_failures(repo):
    code, rep, _ = run_smoke(repo, "enigma", free_port())
    assert code == 0 and rep["result"] == "PASS" and rep["identity"] == "enigma"
    routes = rep["routes"]
    assert routes["/"]["class"] == routes["/sign-in"]["class"] == "ok"
    assert routes["/app"]["class"] == "gated" and routes["/app"]["status"] == 307
    assert routes["/api/enigma/me"]["class"] == "gated" and routes["/api/enigma/me"]["status"] == 401
    assert routes["/app"]["location_path"] == "/sign-in", "redirect query strings must never be reported"
    assert "secret-query" not in json.dumps(rep)

    code, rep, _ = run_smoke(repo, "unconfigured", free_port())
    assert code == 0, "a 503 on protected routes is the expected fail-closed answer, not a failed server"
    assert rep["routes"]["/app"]["class"] == "fail_closed" and rep["routes"]["/"]["class"] == "ok"


def test_timeout_is_distinct_from_an_absent_server(repo):
    code, rep, _ = run_smoke(repo, "hang", free_port())
    assert code == 1 and rep["result"] == "SERVER_UNRESPONSIVE" and "hung" in rep["hint"]
    assert rep["identity_probe"]["outcome"] == "TIMEOUT"

    port = free_port()                                                                   # nothing listening, no --start
    r = subprocess.run([sys.executable, str(SCRIPT), "--project", str(repo), "--port", str(port), "--no-report"], capture_output=True, text=True, timeout=30)
    out = json.loads(r.stdout[r.stdout.index("{"):r.stdout.rindex("\n}") + 2])
    assert r.returncode == 3 and out["result"] == "NO_SERVER"
    assert smoke.http_probe(port, "/", 1).outcome == "NO_SERVER"


def test_connection_reset_is_classified(repo):
    code, rep, _ = run_smoke(repo, "reset", free_port())
    assert code == 1 and rep["identity_probe"]["outcome"] == "RESET"
    assert "ECONNRESET" in rep["hint"] or "reset" in rep["hint"]


def test_unrelated_service_is_not_treated_as_enigma(repo):
    code, rep, _ = run_smoke(repo, "unrelated", free_port())
    assert code == 3 and rep["result"] == "UNRELATED_SERVICE" and "routes" not in rep


# --------------------------------------------------------------------------------------------------------- port conflicts, early exit, redaction
def test_occupied_port_is_detected_and_the_occupant_is_left_alone(repo):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200); self.send_header("content-length", "2"); self.end_headers(); self.wfile.write(b"ok")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        code, rep, _ = run_smoke(repo, "enigma", port)
        assert code == 3 and rep["result"] == "PORT_OCCUPIED" and rep["occupant"]["identity"] == "unknown"
        assert smoke.http_probe(port, "/", 2).status == 200, "the runner must not kill or disturb an unrelated listener"
    finally:
        srv.shutdown()


def test_early_exit_reports_code_classification_and_redacted_log(repo):
    code, rep, out = run_smoke(repo, "exit3", free_port())
    assert code == 3 and rep["result"] == "START_FAILED" and rep["exit_code"] == 3
    assert "SUPERSECRETVALUE123456" not in out and "abc123def456" not in out and "sk_test_" not in out
    tail = out.split("--- redacted server log tail ---", 1)[1]
    assert "boot failed" in tail and ("<clerk-key>" in tail or "<redacted>" in tail), "the useful part of the log is kept, only the secrets are replaced"
    saved = json.loads(next((repo / ".enigma-local").glob("smoke-report-*.json")).read_text())
    assert "SUPERSECRETVALUE123456" not in json.dumps(saved) and "boot failed" in saved["log_tail"]

    code, rep, _ = run_smoke(repo, "eaddrinuse", free_port())
    assert rep["classification"] == "PORT_OCCUPIED" and rep["exit_code"] == 1


def test_redact_covers_keys_tokens_cookies_arns_and_emails():
    raw = ("sk_live_abcdefghijk pk_test_zzzzzzzz eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sigsigsig AKIAABCDEFGHIJKLMNOP "
           "arn:aws:iam::123456789012:user/x Cookie: enigma_at=abc; enigma_rt=def password=hunter2 a.b@example.com")
    out = smoke.redact(raw)
    for leaked in ("abcdefghijk", "zzzzzzzz", "eyJhbGci", "AKIAABCDEFGHIJKLMNOP", "123456789012", "enigma_at=abc", "hunter2", "a.b@example.com"):
        assert leaked not in out, leaked


# --------------------------------------------------------------------------------------------------------- process hygiene / report
def test_only_the_launched_process_group_is_stopped_and_the_port_is_freed(repo):
    decoy = subprocess.Popen(["sleep", "300"], start_new_session=True)                    # an unrelated process the runner must never touch
    try:
        port = free_port()
        code, rep, _ = run_smoke(repo, "enigma", port)
        assert code == 0 and rep["port_free_after"] is True and rep["stopped"]["exit_code"] is not None
        assert not smoke.port_in_use(port)
        assert decoy.poll() is None, "unrelated process was terminated"
    finally:
        os.killpg(decoy.pid, signal.SIGKILL)
        decoy.wait()


def test_machine_readable_report_is_written_under_enigma_local(repo):
    port = free_port()
    code, rep, out = run_smoke(repo, "enigma", port)
    files = list((repo / ".enigma-local").glob("smoke-report-*.json"))
    assert code == 0 and len(files) == 1
    saved = json.loads(files[0].read_text())
    assert saved["result"] == "PASS" and saved["port"] == port and "routes" in saved


def test_server_log_flag_turns_a_pass_into_a_fail(repo):
    """If the launched server logs 'Failed to proxy', the run cannot pass even when the probed statuses look fine (the original defect's signature)."""
    stub = (repo.parent / "stub.py")
    stub.write_text(STUB.replace('mode, port = sys.argv[1], int(sys.argv[2])', 'mode, port = sys.argv[1], int(sys.argv[2])\nprint("Failed to proxy http://localhost:%d/ Error: socket hang up" % port, flush=True)'))
    code, rep, _ = run_smoke(repo, "enigma", free_port())
    assert code == 1 and rep["result"] == "FAIL" and rep["server_log_flags"]["failed_to_proxy"] is True


# --------------------------------------------------------------------------------------------------------- auth-mode environments
def test_child_environment_never_leaks_cloud_gpu_or_proxy_settings(monkeypatch):
    for k, v in {"AWS_PROFILE": "x", "AWS_ACCESS_KEY_ID": "y", "CUDA_VISIBLE_DEVICES": "0", "NVIDIA_VISIBLE_DEVICES": "all", "HTTPS_PROXY": "http://p", "http_proxy": "http://p"}.items():
        monkeypatch.setenv(k, v)
    env = smoke.child_env(False)
    assert not any(k.startswith(("AWS_", "CUDA_", "NVIDIA_")) for k in env) and not {"HTTPS_PROXY", "http_proxy"} & set(env)
    assert env["NO_PROXY"] == "127.0.0.1,localhost" and env["CLERK_TELEMETRY_DISABLED"] == "1"


def test_unconfigured_and_dev_auth_modes_blank_the_clerk_variables_and_dev_auth_gets_a_throwaway_secret(monkeypatch):
    monkeypatch.delenv("ENIGMA_DEV_JWT_SECRET", raising=False)
    un = smoke.child_env(True)
    assert un["CLERK_SECRET_KEY"] == "" and un["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"] == "" and un["ENIGMA_DEV_AUTH"] == ""
    dev = smoke.child_env(False, True)
    assert dev["ENIGMA_DEV_AUTH"] == "1" and len(dev["ENIGMA_DEV_JWT_SECRET"]) >= 32 and dev["CLERK_SECRET_KEY"] == "" and dev["ENIGMA_AUTH_PROVIDER"] == ""
    assert dev["ENIGMA_API_URL"].startswith("http://127.0.0.1:")
    monkeypatch.setenv("ENIGMA_DEV_JWT_SECRET", "z" * 40)
    assert smoke.child_env(False, True)["ENIGMA_DEV_JWT_SECRET"] == "z" * 40, "an explicit secret is shared so a local API can verify the BFF's tokens"


def test_dev_auth_requires_a_loopback_api_and_excludes_unconfigured(repo):
    for extra in (["--dev-auth", "--unconfigured"], ["--dev-auth", "--api-url", "https://api.example.com"]):
        r = subprocess.run([sys.executable, str(SCRIPT), "--project", str(repo), "--no-report", *extra], capture_output=True, text=True, timeout=30)
        assert r.returncode == 2, extra


def test_unlisted_bff_path_must_never_be_served_and_404_is_its_contract():
    assert smoke.classify("unlisted", smoke.Probe("HTTP", 404))[0] == "gated"
    assert smoke.classify("unlisted", smoke.Probe("HTTP", 307))[0] == "gated"
    assert smoke.classify("unlisted", smoke.Probe("HTTP", 200))[0] == "fail"
    assert smoke.classify("unlisted", smoke.Probe("HTTP", 502))[0] == "fail"
    assert smoke.classify("protected", smoke.Probe("HTTP", 502))[0] == "fail", "an unreachable upstream is a failure, not an auth gate"
    assert smoke.classify("protected", smoke.Probe("HTTP", 503))[0] == "fail_closed"
    assert smoke.classify("public", smoke.Probe("HTTP", 307))[0] == "fail"
    for outcome in ("TIMEOUT", "RESET", "NO_SERVER"):
        assert smoke.classify("protected", smoke.Probe(outcome))[0] == "fail"
