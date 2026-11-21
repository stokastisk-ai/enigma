"""JobExecutor against the REAL API app over real localhost HTTP."""
from __future__ import annotations

import time
import urllib.error
import urllib.request

import httpx
import pytest

from enigma_connector.http import ApiClient, ApiError, AuthError
from enigma_connector.jobs import JobExecutor, classify_error
from enigma_connector.mock import MockLlm


class Upstream(Exception):
    def __init__(self, msg, status=None, body=""):
        super().__init__(msg)
        self.status, self.body = status, body


class ScriptedLlm:
    """Yield `chunks` with a delay; optionally raise after N chunks. Records whether the generator was closed (cancel path)."""
    def __init__(self, chunks, delay=0.0, first_delay=0.0, fail_after=None, error=None):
        self.chunks, self.delay, self.first_delay, self.fail_after, self.error = chunks, delay, first_delay, fail_after, error
        self.closed = False
        self.calls = 0

    def stream(self, messages, *, max_tokens, temperature, model):
        self.calls += 1
        try:
            time.sleep(self.first_delay)
            for i, c in enumerate(self.chunks):
                if self.fail_after is not None and i == self.fail_after and self.calls == 1:
                    raise self.error
                yield c
                time.sleep(self.delay)
        finally:
            self.closed = True


def submit_and_claim(t, api, text="hello"):
    t.register()
    conv = t.conversation()
    job_id = t.submit(conv, text).json()["job_id"]
    job = api.post("/api/v1/jobs/claim", {"lease_seconds": 30})["job"]
    assert job and job["job_id"] == job_id
    return conv, job_id, job


def view(t, job_id, after=0):
    return httpx.get(f"{t.c.c.base_url}/api/v1/app/jobs/{job_id}", params={"after_seq": after}, headers=t.h).json()


def messages(t, conv):
    return httpx.get(f"{t.c.c.base_url}/api/v1/app/conversations/{conv}/messages", headers=t.h).json()["items"]


def test_stream_is_ordered_batched_and_final_text_matches(tenant, api, cfg):
    conv, job_id, job = submit_and_claim(tenant, api)
    chunks = [f"w{i} " for i in range(60)]
    llm = ScriptedLlm(chunks, delay=0.004, first_delay=0.02)
    assert JobExecutor(api, llm, cfg, is_mock=True).run(job) == "succeeded"
    v = view(tenant, job_id)
    seqs = [e["seq"] for e in v["events"]]
    assert seqs == list(range(1, len(seqs) + 1)) and len(seqs) < 60                  # contiguous, and batched (not one event per chunk)
    assert "".join(e["text"] for e in v["events"]) == "".join(chunks)
    assert v["events"][0]["text"] == "w0 "                                            # the first chunk is flushed immediately (browser TTFT)
    msgs = messages(tenant, conv)
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["content"] == "".join(chunks)
    m = msgs[-1]["meta"]["measured"]
    assert m["stream_chunks"] == 60 and m["ttft_ms"] >= 20 and m["e2e_ms"] >= m["ttft_ms"] and m["is_mock"] is True and "completion_tokens" not in m
    assert v["status"] == "succeeded" and v["result"]["text_sha256"]


def test_empty_stream_completes_with_empty_text_and_null_ttft(tenant, api, cfg):
    conv, job_id, job = submit_and_claim(tenant, api)
    assert JobExecutor(api, ScriptedLlm([]), cfg).run(job) == "succeeded"
    m = [x for x in messages(tenant, conv) if x["role"] == "assistant"][0]
    assert m["content"] == "" and "ttft_ms" not in m["meta"]["measured"]              # unknown stays absent, never 0


def test_cancel_mid_stream_stops_generation_and_keeps_partial(tenant, api, cfg):
    conv, job_id, job = submit_and_claim(tenant, api)
    llm = ScriptedLlm([f"t{i} " for i in range(400)], delay=0.02)
    import threading
    out = {}
    th = threading.Thread(target=lambda: out.update(r=JobExecutor(api, llm, cfg).run(job)))
    th.start()
    for _ in range(100):                                                               # wait until tokens are flowing
        if view(tenant, job_id)["events"]:
            break
        time.sleep(0.05)
    assert httpx.post(f"{tenant.c.c.base_url}/api/v1/app/jobs/{job_id}/cancel", headers=tenant.h).json()["status"] == "cancel_requested"
    th.join(10)
    assert out["r"] == "cancelled" and llm.closed is True                              # generator closed => SSE connection closed => vLLM aborts
    final = [m for m in messages(tenant, conv) if m["role"] == "assistant"][0]
    assert final["meta"] == {"cancelled": True} and final["content"].startswith("t0 ") and len(final["content"]) < 400 * 4
    assert view(tenant, job_id)["status"] == "cancelled"


def test_cancel_noticed_during_long_prefill_via_keepalive(tenant, api, cfg):
    conv, job_id, job = submit_and_claim(tenant, api)
    llm = ScriptedLlm(["x "], first_delay=3.0)                                         # no token for 3 s; keepalive is every 0.3 s
    import threading
    out = {}
    th = threading.Thread(target=lambda: out.update(r=JobExecutor(api, llm, cfg).run(job)))
    th.start()
    time.sleep(0.8)
    assert any(e["kind"] == "status" for e in view(tenant, job_id)["events"])          # keepalive events renewed the lease
    httpx.post(f"{tenant.c.c.base_url}/api/v1/app/jobs/{job_id}/cancel", headers=tenant.h)
    th.join(10)
    assert out["r"] == "cancelled"
    assert [m for m in messages(tenant, conv) if m["role"] == "assistant"] == []        # nothing generated yet -> no empty assistant message


def test_retryable_error_requeues_and_second_attempt_resets_text(tenant, api, cfg):
    conv, job_id, job = submit_and_claim(tenant, api)
    llm = ScriptedLlm(["a ", "b ", "c ", "d "], fail_after=2, error=Upstream("stream ended without a [DONE] marker (truncated)"))
    assert JobExecutor(api, llm, cfg).run(job) == "failed"
    assert view(tenant, job_id)["status"] == "pending" and view(tenant, job_id)["error_code"] == "stream_truncated"
    job2 = api.post("/api/v1/jobs/claim", {})["job"]
    assert job2["attempt"] == 2 and job2["next_event_seq"] > 1
    assert JobExecutor(api, llm, cfg).run(job2) == "succeeded"
    kinds = [e["kind"] for e in view(tenant, job_id)["events"]]
    assert "reset" in kinds
    assert [m for m in messages(tenant, conv) if m["role"] == "assistant"][0]["content"] == "a b c d "        # attempt-1 text discarded


def test_non_retryable_upstream_error_fails_without_leaking_body(tenant, api, cfg):
    conv, job_id, job = submit_and_claim(tenant, api, text="my secret prompt text")
    llm = ScriptedLlm(["x "], fail_after=0, error=Upstream("POST failed", status=400, body="maximum context length ... my secret prompt text"))
    assert JobExecutor(api, llm, cfg).run(job) == "failed"
    v = view(tenant, job_id)
    assert v["status"] == "failed" and v["error_code"] == "upstream_400" and "secret" not in str(v)


@pytest.mark.parametrize("err,expect", [
    (Upstream("x", status=400), ("upstream_400", False)), (Upstream("x", status=503), ("upstream_503", True)),
    (Upstream("POST u could not reach the server: refused"), ("upstream_unreachable", True)),
    (Upstream("the stream ended without a [DONE] marker (truncated)"), ("stream_truncated", True)), (ValueError("boom"), ("connector_error", False))])
def test_error_classification(err, expect):
    code, msg, retry = classify_error(err)
    assert (code, retry) == expect and "boom" not in msg


def test_lease_loss_abandons_without_completing(tenant, api, cfg, live):
    conv, job_id, job = submit_and_claim(tenant, api)
    live.store.jobs[job_id]["lease_owner"] = "someone-else"                           # another worker took over
    assert JobExecutor(api, ScriptedLlm(["a ", "b "], delay=0.05), cfg).run(job) == "abandoned"
    assert view(tenant, job_id)["status"] != "succeeded" and [m for m in messages(tenant, conv) if m["role"] == "assistant"] == []


def test_lost_responses_do_not_duplicate_or_lose_text(tenant, api, cfg, live):
    """The API processes the request but the response is lost (timeout). The connector resends: the server dedups by event_seq."""
    conv, job_id, job = submit_and_claim(tenant, api)
    calls = {"n": 0}
    real = urllib.request.urlopen

    def flaky(req, timeout):
        resp = real(req, timeout=timeout)
        if "/events" in req.full_url:
            calls["n"] += 1
            if calls["n"] % 2 == 1:
                resp.read()
                raise urllib.error.URLError("response lost")
        return resp
    flaky_api = ApiClient(cfg, flaky)
    chunks = [f"c{i} " for i in range(30)]
    assert JobExecutor(flaky_api, ScriptedLlm(chunks, delay=0.01), cfg).run(job) == "succeeded"
    assert "".join(e["text"] for e in view(tenant, job_id)["events"]) == "".join(chunks)
    assert [m["content"] for m in messages(tenant, conv) if m["role"] == "assistant"] == ["".join(chunks)]
    assert calls["n"] >= 2


def test_unrecoverable_gap_raises_instead_of_looping(tenant, api, cfg):
    conv, job_id, job = submit_and_claim(tenant, api)
    ex = JobExecutor(api, ScriptedLlm([]), cfg)
    with pytest.raises(ApiError) as e:
        ex._send(job_id, [{"event_seq": 5, "kind": "delta", "text": "x"}])            # server expects 1
    assert e.value.code == "sequence_gap" and e.value.extra["expected_seq"] == 1


def test_revoked_token_mid_job_is_fatal(tenant, api, cfg):
    conv, job_id, job = submit_and_claim(tenant, api)
    httpx.post(f"{tenant.c.c.base_url}/api/v1/app/runtimes/{tenant.runtime_id}/revoke", headers=tenant.h)
    with pytest.raises(AuthError):
        JobExecutor(api, ScriptedLlm(["a "] * 5, delay=0.05), cfg).run(job)


def test_mock_llm_marks_output_and_respects_max_tokens():
    out = "".join(MockLlm(0, 0).stream([{"role": "user", "content": "hi there"}], max_tokens=5, temperature=0, model=None))
    assert out.startswith("[MOCK MODEL") and len(out.split()) <= 5
