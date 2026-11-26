"""Loopback vLLM traffic must never be routed through an HTTP proxy; remote Enigma API traffic keeps honouring proxy settings (CPU only)."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import urllib.request
from pathlib import Path

import pytest

from enigma_connector import localnet
from enigma_connector.http import ApiClient
from enigma_connector.config import Config

CONNECTOR = str(Path(__file__).resolve().parents[2] / "connector")

SCRIPT = textwrap.dedent("""
    import sys, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    sys.path.insert(0, sys.argv[1])
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            body = b'{"version": "0.25.1"}' if self.path == "/version" else b"vllm:num_requests_running 0\\n"
            self.send_response(200); self.end_headers(); self.wfile.write(body)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    from enigma_connector.vllm_metrics import VllmSampler
    from enigma_connector.identity import fetch_vllm_version
    s = VllmSampler(base)
    print("metrics_ok", s.sample() is not None, s.last_error)
    print("version", fetch_vllm_version(base))
""")


def _run(env_extra):
    env = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "no_proxy", "all_proxy")}
    env.update(env_extra)
    r = subprocess.run([sys.executable, "-c", SCRIPT, CONNECTOR], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr[-800:]
    return r.stdout


@pytest.mark.parametrize("proxy_var", ["http_proxy", "HTTP_PROXY", "all_proxy"])
def test_local_vllm_scrape_and_version_ignore_a_dead_proxy(proxy_var):
    out = _run({proxy_var: "http://127.0.0.1:9"})                    # nothing listens on :9; before the fix this produced URLError
    assert "metrics_ok True None" in out and "version 0.25.1" in out


def test_open_loopback_refuses_any_other_host():
    for url in ("https://example.com/metrics", "http://10.0.0.1:8000/metrics", "http://127.0.0.1.example.com/x"):
        with pytest.raises(ValueError):
            localnet.open_loopback(url, timeout=1)


def test_ensure_loopback_bypass_appends_once_and_keeps_existing_entries():
    env = {"no_proxy": "corp.internal, .example.org"}
    localnet.ensure_loopback_bypass(env)
    localnet.ensure_loopback_bypass(env)
    assert env["no_proxy"] == "corp.internal,.example.org,127.0.0.1,localhost,::1"
    assert env["NO_PROXY"] == "127.0.0.1,localhost,::1"


def test_the_remote_api_client_still_uses_the_standard_proxy_aware_opener():
    cfg = Config(api_url="https://api.test", token="enig_rt_abcdefgh_" + "A" * 43, instance_id="t").validate()
    assert ApiClient(cfg)._open is urllib.request.urlopen                     # TLS verification and proxy behaviour unchanged for Enigma
