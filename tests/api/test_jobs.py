"""Job lifecycle over HTTP: claim, ordered events, idempotency, leases, cancel, retry, completion integrity."""
from __future__ import annotations

from api_helpers import sha


def _start(alice, text="hello"):
    conv = alice.conversation()
    job_id = alice.submit(conv, text).json()["job_id"]
    claimed = alice.claim().json()["job"]
    assert claimed["job_id"] == job_id
    return conv, job_id, claimed


def d(seq, text, kind="delta"):
    return {"event_seq": seq, "kind": kind, "text": text}


def test_full_happy_path_persists_final_message_once(client, alice, bus):
    conv, job_id, claimed = _start(alice)
    assert claimed["next_event_seq"] == 1 and claimed["attempt"] == 1
    assert claimed["request"]["messages"][-1] == {"role": "user", "content": "hello"}
    r = alice.events(job_id, [d(1, "Hel"), d(2, "lo "), d(3, "world")])
    assert r.json() == {"ok": True, "last_event_seq": 3, "accepted": 3, "duplicates": 0, "cancel_requested": False}
    # polling fallback sees the deltas in order, and only after a cursor
    v = client.get(f"/api/v1/app/jobs/{job_id}?after_seq=1", headers=alice.h).json()
    assert [e["seq"] for e in v["events"]] == [2, 3] and v["status"] == "running" and v["first_token_at"]
    done = client.post(f"/api/v1/jobs/{job_id}/complete", headers=alice.rh,
                       json={"final_seq": 3, "text_sha256": sha("Hello world"), "result": {"ttft_ms": 41.5, "e2e_ms": 300.0, "stream_chunks": 3, "is_mock": True}})
    assert done.status_code == 200 and done.json()["assistant_message_id"]
    msgs = client.get(f"/api/v1/app/conversations/{conv}/messages", headers=alice.h).json()["items"]
    assert [(m["role"], m["content"]) for m in msgs] == [("user", "hello"), ("assistant", "Hello world")]
    assert msgs[1]["meta"]["measured"]["ttft_ms"] == 41.5 and msgs[1]["meta"]["measured"]["is_mock"] is True
    # idempotent completion: same hash ok, different hash conflicts, still one assistant message
    assert client.post(f"/api/v1/jobs/{job_id}/complete", headers=alice.rh, json={"final_seq": 3, "text_sha256": sha("Hello world")}).json()["idempotent"] is True
    assert client.post(f"/api/v1/jobs/{job_id}/complete", headers=alice.rh, json={"final_seq": 3, "text_sha256": sha("other")}).status_code == 409
    assert sum(1 for m in client.get(f"/api/v1/app/conversations/{conv}/messages", headers=alice.h).json()["items"] if m["role"] == "assistant") == 1
    # realtime: private-channel broadcasts carried the deltas and terminal status, addressed to this conversation only
    kinds = [(c, e) for c, e, _ in bus.sent]
    assert all(c == conv for c, _ in kinds) and ("status" in [e for _, e in kinds]) and ("events" in [e for _, e in kinds])
    ev = next(p for c, e, p in bus.sent if e == "events")
    assert [x["text"] for x in ev["events"]] == ["Hel", "lo ", "world"]
    assert bus.sent[-1][1] == "status" and bus.sent[-1][2]["status"] == "succeeded"


def test_duplicate_and_out_of_order_events(client, alice):
    _, job_id, _ = _start(alice)
    assert alice.events(job_id, [d(2, "b"), d(1, "a")]).json()["accepted"] == 2          # sorted server-side
    r = alice.events(job_id, [d(1, "a"), d(2, "b")])                                     # replay of an already-acked batch
    assert r.json()["duplicates"] == 2 and r.json()["accepted"] == 0 and r.json()["last_event_seq"] == 2
    r = alice.events(job_id, [d(5, "x")])                                                # gap
    assert r.status_code == 409 and r.json()["error"]["code"] == "sequence_gap" and r.json()["error"]["expected_seq"] == 3
    assert alice.events(job_id, [d(3, "c"), d(4, "d")]).json()["last_event_seq"] == 4    # resend from expected_seq
    assert client.get(f"/api/v1/app/jobs/{job_id}", headers=alice.h).json()["events"][-1]["seq"] == 4


def test_complete_rejects_hash_and_sequence_mismatch(client, alice):
    _, job_id, _ = _start(alice)
    alice.events(job_id, [d(1, "abc")])
    r = client.post(f"/api/v1/jobs/{job_id}/complete", headers=alice.rh, json={"final_seq": 1, "text_sha256": sha("tampered")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "hash_mismatch"
    r = client.post(f"/api/v1/jobs/{job_id}/complete", headers=alice.rh, json={"final_seq": 9, "text_sha256": sha("abc")})
    assert r.status_code == 409 and r.json()["error"]["expected_seq"] == 1
    assert client.get(f"/api/v1/app/jobs/{job_id}", headers=alice.h).json()["status"] == "running"      # nothing was finalised


def test_event_payload_validation(client, alice):
    _, job_id, _ = _start(alice)
    assert alice.events(job_id, []).status_code == 422
    assert alice.events(job_id, [d(1, "x" * 9000)]).status_code == 422
    assert alice.events(job_id, [{"event_seq": 0, "text": "x"}]).status_code == 422
    assert alice.events(job_id, [{"event_seq": 1, "text": "x", "evil": 1}]).status_code == 422
    assert alice.events(job_id, [d(i, "x") for i in range(1, 70)]).status_code == 422                    # > 64 events per request


def test_stale_lease_reclaim_resets_and_locks_out_old_worker(client, clock, alice):
    conv, job_id, claimed = _start(alice)
    alice.events(job_id, [d(1, "partial ")])
    clock.advance(31)                                                                    # lease (30 s) expires; connector died
    stale = alice.events(job_id, [d(2, "late")])
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "lease_lost"
    other = {**alice.rh, "X-Enigma-Instance": "inst-2"}
    again = client.post("/api/v1/jobs/claim", json={}, headers=other).json()["job"]
    assert again["job_id"] == job_id and again["attempt"] == 2 and again["next_event_seq"] == 2
    assert alice.events(job_id, [d(2, "late")]).status_code == 409                      # the old instance is locked out for good
    ok = client.post(f"/api/v1/jobs/{job_id}/events", headers=other, json={"events": [d(2, "", "reset"), d(3, "fresh")]})
    assert ok.json()["accepted"] == 2
    done = client.post(f"/api/v1/jobs/{job_id}/complete", headers=other, json={"final_seq": 3, "text_sha256": sha("fresh")})
    assert done.status_code == 200
    final = [m for m in client.get(f"/api/v1/app/conversations/{conv}/messages", headers=alice.h).json()["items"] if m["role"] == "assistant"]
    assert [m["content"] for m in final] == ["fresh"]                                    # partial text of attempt 1 was discarded


def test_exhausted_attempts_fail_instead_of_looping(client, clock, alice):
    _, job_id, _ = _start(alice)
    clock.advance(31)
    assert client.post("/api/v1/jobs/claim", json={}, headers={**alice.rh, "X-Enigma-Instance": "i2"}).json()["job"]["attempt"] == 2
    clock.advance(31)
    assert client.post("/api/v1/jobs/claim", json={}, headers={**alice.rh, "X-Enigma-Instance": "i3"}).json()["job"] is None
    v = client.get(f"/api/v1/app/jobs/{job_id}", headers=alice.h).json()
    assert v["status"] == "failed" and v["error_code"] == "lease_expired"


def test_lease_renewed_by_events(client, clock, alice):
    _, job_id, _ = _start(alice)
    for i in range(1, 5):
        clock.advance(20)                                                               # each batch renews the 30 s lease
        assert alice.events(job_id, [d(i, "x")]).status_code == 200


def test_cancel_pending_job_is_immediate_and_never_claimed(client, alice):
    conv = alice.conversation()
    job_id = alice.submit(conv).json()["job_id"]
    assert client.post(f"/api/v1/app/jobs/{job_id}/cancel", headers=alice.h).json()["status"] == "cancelled"
    assert alice.claim().json()["job"] is None
    assert client.post(f"/api/v1/app/jobs/{job_id}/cancel", headers=alice.h).json()["status"] == "cancelled"     # idempotent


def test_cancel_running_job_round_trip_keeps_partial_text(client, alice, bus):
    conv, job_id, _ = _start(alice)
    alice.events(job_id, [d(1, "par"), d(2, "tial")])
    assert client.post(f"/api/v1/app/jobs/{job_id}/cancel", headers=alice.h).json()["status"] == "cancel_requested"
    r = alice.events(job_id, [d(3, "!")])
    assert r.json()["cancel_requested"] is True                                          # connector learns about it on its next upload
    assert client.get(f"/api/v1/jobs/{job_id}/status", headers=alice.rh).json()["cancel_requested"] is True
    ack = client.post(f"/api/v1/jobs/{job_id}/ack-cancel", headers=alice.rh)
    assert ack.json()["status"] == "cancelled"
    assert client.post(f"/api/v1/jobs/{job_id}/ack-cancel", headers=alice.rh).json()["idempotent"] is True
    msgs = client.get(f"/api/v1/app/conversations/{conv}/messages", headers=alice.h).json()["items"]
    assert msgs[-1]["content"] == "partial!" and msgs[-1]["meta"] == {"cancelled": True}
    assert alice.events(job_id, [d(4, "x")]).status_code == 409                         # nothing after terminal
    assert bus.sent[-1][2]["status"] == "cancelled"


def test_retryable_failure_requeues_then_terminal(client, alice):
    _, job_id, _ = _start(alice)
    r = client.post(f"/api/v1/jobs/{job_id}/fail", headers=alice.rh, json={"code": "oom", "message": "gpu oom", "retryable": True})
    assert r.json()["status"] == "pending"
    again = alice.claim().json()["job"]
    assert again["attempt"] == 2
    r = client.post(f"/api/v1/jobs/{job_id}/fail", headers=alice.rh, json={"code": "oom", "message": "gpu oom again", "retryable": True})
    assert r.json()["status"] == "failed"                                                # attempts exhausted: no infinite retry
    v = client.get(f"/api/v1/app/jobs/{job_id}", headers=alice.h).json()
    assert v["status"] == "failed" and v["error_code"] == "oom"


def test_runtime_cannot_touch_another_runtimes_job(client, alice, bob):
    _, job_id, _ = _start(alice)
    assert bob.events(job_id, [d(1, "evil")]).status_code == 404
    assert client.post(f"/api/v1/jobs/{job_id}/complete", headers=bob.rh, json={"final_seq": 0, "text_sha256": sha("")}).status_code == 404
    assert client.post(f"/api/v1/jobs/{job_id}/fail", headers=bob.rh, json={"code": "x", "message": "m"}).status_code == 404
    assert client.get(f"/api/v1/jobs/{job_id}/status", headers=bob.rh).status_code == 404
    assert bob.claim().json()["job"] is None


def test_submit_idempotency_and_caps(client, alice):
    conv = alice.conversation()
    a = alice.submit(conv, key="same-key-123456").json()
    b = alice.submit(conv, key="same-key-123456").json()
    assert a["job_id"] == b["job_id"] and b["idempotent"] is True
    assert len([m for m in client.get(f"/api/v1/app/conversations/{conv}/messages", headers=alice.h).json()["items"] if m["role"] == "user"]) == 1
    # the Idempotency-Key header is mandatory
    assert client.post(f"/api/v1/app/conversations/{conv}/jobs", json={"runtime_id": alice.runtime_id, "content": "x"}, headers=alice.h).status_code == 422
    # per-user active-job cap (2) -> 429
    assert alice.submit(conv).status_code == 202
    r = alice.submit(conv)
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_user_jobs"


def test_prompt_and_max_token_budgets(client, alice):
    conv = alice.conversation()
    r = alice.submit(conv, content="x" * 5800)                                           # (2048-256)*3-200 = 5176 chars budget
    assert r.status_code == 422 and r.json()["error"]["code"] == "prompt_too_long" and r.json()["error"]["char_budget"] == 5176
    assert alice.submit(conv, max_tokens=1024).status_code == 422                        # >= half the context window
    assert alice.submit(conv, max_tokens=513).status_code == 422
    assert alice.submit(conv, temperature=3).status_code == 422


def test_submit_refused_when_runtime_offline_and_recovers_on_heartbeat(client, clock, alice):
    conv = alice.conversation()
    clock.advance(40)
    assert client.get(f"/api/v1/app/projects/{alice.project_id}/runtimes", headers=alice.h).json()["items"][0]["status"] == "stale"
    assert alice.submit(conv).status_code == 202                                         # stale is still accepted (heartbeat may be late)
    clock.advance(200)
    r = alice.submit(conv)
    assert r.status_code == 409 and r.json()["error"]["code"] == "runtime_offline" and r.json()["error"]["runtime_status"] == "offline"
    assert client.post(f"/api/v1/runtimes/{alice.runtime_id}/heartbeat", headers=alice.rh).status_code == 200
    assert client.get(f"/api/v1/app/projects/{alice.project_id}/runtimes", headers=alice.h).json()["items"][0]["status"] == "online"


def test_pending_job_expires_at_deadline(client, clock, alice):
    conv = alice.conversation()
    job_id = alice.submit(conv).json()["job_id"]
    clock.advance(601)
    assert client.get(f"/api/v1/app/jobs/{job_id}", headers=alice.h).json()["status"] == "expired"
    assert alice.claim().json()["job"] is None


def test_history_is_trimmed_to_budget(client, alice):
    conv = alice.conversation()
    for i in range(2):
        jid = alice.submit(conv, content=f"q{i} " + "y" * 1500).json()["job_id"]
        job = alice.claim().json()["job"]
        alice.events(jid, [d(1, "a" * 1500)])
        client.post(f"/api/v1/jobs/{jid}/complete", headers=alice.rh, json={"final_seq": 1, "text_sha256": sha("a" * 1500)})
        assert job["job_id"] == jid
    jid = alice.submit(conv, content="latest question").json()["job_id"]
    msgs = alice.claim().json()["job"]["request"]["messages"]
    assert msgs[-1]["content"] == "latest question"
    assert sum(len(m["content"]) for m in msgs) <= 5176 and len(msgs) < 5                 # oldest turns dropped
