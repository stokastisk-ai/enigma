"""Real ``stokastisk.serving.stream_chat`` (imported read-only from the frozen SDK source) against a FAKE OpenAI-compatible local server.

This proves the connector drives the SDK's public streaming API correctly. It does NOT prove anything about a GPU or real vLLM
(see docs/TEST_REPORT.md: GPU acceptance is BLOCKED until an authorised Kaggle run)."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

SDK_SRC = Path(os.environ.get("ENIGMA_SDK_SRC", Path(__file__).resolve().parents[3] / "Project-Stokastisk" / "sdk"))
pytestmark = pytest.mark.skipif(not (SDK_SRC / "stokastisk" / "serving.py").exists(), reason="Stokastisk SDK source not found (set ENIGMA_SDK_SRC)")

FIX = Path(__file__).parent / "fixtures" / "vllm_metrics_synthetic.txt"


@pytest.fixture(scope="module")
def sdk():
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True                       # never write .pyc into the frozen SDK tree
    sys.path.insert(0, str(SDK_SRC))
    try:
        import stokastisk.serving as serving
        yield serving
    finally:
        sys.path.remove(str(SDK_SRC))
        sys.dont_write_bytecode = old
        for m in [m for m in sys.modules if m == "stokastisk" or m.startswith("stokastisk.")]:
            del sys.modules[m]


class FakeVllm:
    def __init__(self):
        self.disconnects = 0
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self):
                if self.path == "/health":
                    self.send_response(200); self.end_headers()
                elif self.path == "/metrics":
                    body = FIX.read_bytes()
                    self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                else:
                    self.send_response(404); self.end_headers()

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(req)
                model = req["model"]
                if model == "bad400":
                    body = json.dumps({"error": {"message": "maximum context length exceeded: <prompt text>"}}).encode()
                    self.send_response(400); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                words = ["Hello", " from", " fake", " vLLM", "."] if model != "slow" else [f" w{i}" for i in range(500)]
                try:
                    for w in words:
                        self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": w}}]}).encode() + b"\n\n")
                        self.wfile.flush()
                        if model == "slow":
                            time.sleep(0.02)
                    if model != "truncate":
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    outer.disconnects += 1

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def fake():
    f = FakeVllm()
    yield f
    f.close()


def run_job(tenant, api, cfg, fake, model, sdk):
    from enigma_connector.jobs import JobExecutor, StokastiskLlm
    tenant.register()
    conv = tenant.conversation()
    job_id = tenant.submit(conv, "what is a T4?").json()["job_id"]
    job = api.post("/api/v1/jobs/claim", {})["job"]
    job["request"]["model"] = model
    llm = StokastiskLlm(fake.url, model, read_timeout=5.0)
    return conv, job_id, JobExecutor(api, llm, cfg), job


def test_real_stream_chat_end_to_end(sdk, tenant, api, cfg, fake):
    conv, job_id, ex, job = run_job(tenant, api, cfg, fake, "fake-model", sdk)
    assert ex.run(job) == "succeeded"
    msgs = httpx.get(f"{cfg.api_url}/api/v1/app/conversations/{conv}/messages", headers=tenant.h).json()["items"]
    assert msgs[-1]["content"] == "Hello from fake vLLM."
    m = msgs[-1]["meta"]["measured"]
    assert m["stream_chunks"] == 5 and m["ttft_ms"] > 0 and "completion_tokens" not in m            # chunks are NOT claimed to be tokens
    sent = fake.requests[0]                                                                          # what the SDK actually put on the wire
    assert sent["stream"] is True and sent["max_tokens"] == 256 and sent["messages"][-1] == {"role": "user", "content": "what is a T4?"}


def test_truncated_sdk_stream_is_retryable(sdk, tenant, api, cfg, fake):
    conv, job_id, ex, job = run_job(tenant, api, cfg, fake, "truncate", sdk)
    assert ex.run(job) == "failed"
    v = httpx.get(f"{cfg.api_url}/api/v1/app/jobs/{job_id}", headers=tenant.h).json()
    assert v["status"] == "pending" and v["error_code"] == "stream_truncated"                        # SDK raises on a missing [DONE]; we requeue


def test_sdk_http_400_is_terminal_and_hides_upstream_body(sdk, tenant, api, cfg, fake):
    conv, job_id, ex, job = run_job(tenant, api, cfg, fake, "bad400", sdk)
    assert ex.run(job) == "failed"
    v = httpx.get(f"{cfg.api_url}/api/v1/app/jobs/{job_id}", headers=tenant.h).json()
    assert v["status"] == "failed" and v["error_code"] == "upstream_400" and "<prompt text>" not in json.dumps(v)


def test_cancel_closes_the_sdk_connection(sdk, tenant, api, cfg, fake):
    conv, job_id, ex, job = run_job(tenant, api, cfg, fake, "slow", sdk)
    out = {}
    th = threading.Thread(target=lambda: out.update(r=ex.run(job)))
    th.start()
    for _ in range(100):
        if httpx.get(f"{cfg.api_url}/api/v1/app/jobs/{job_id}", headers=tenant.h).json()["events"]:
            break
        time.sleep(0.05)
    httpx.post(f"{cfg.api_url}/api/v1/app/jobs/{job_id}/cancel", headers=tenant.h)
    th.join(15)
    assert out["r"] == "cancelled"
    for _ in range(50):                                                                              # the server must notice the client hung up
        if fake.disconnects:
            break
        time.sleep(0.1)
    assert fake.disconnects >= 1


def test_vllm_sampler_over_real_http_with_sdk_free_parser(fake):
    from enigma_connector.vllm_metrics import VllmSampler
    s = VllmSampler(fake.url).sample()
    assert s["num_requests_running"] == 3.0 and s["ttft_hist"]["count"] == 12.0


def test_sdk_tree_untouched():
    """The import must not have written bytecode into the frozen SDK tree."""
    pyc = [p for p in (SDK_SRC / "stokastisk").rglob("*.pyc") if p.stat().st_mtime > time.time() - 3600]
    assert pyc == []
