"""In-memory Store for unit tests and the local mock E2E. It mirrors the SQL functions' semantics; it is NOT durable and
Settings.validate_for_startup() refuses it in production. State dies with the process (fine for tests, wrong for any deployment)."""
from __future__ import annotations

import copy
import hashlib
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .base import Row, Store

CAPS = {"job_events": 5000, "job_text_chars": 60000, "active_jobs_per_runtime": 4, "active_jobs_per_user": 2, "gpu_rows": 600, "vllm_rows": 120}
TERMINAL = ("succeeded", "failed", "cancelled", "expired")
ACTIVE = ("pending", "claimed", "running", "cancel_requested")
ROLE_RANK = {"owner": 4, "admin": 3, "member": 2, "viewer": 1}


def _uid() -> str:
    return str(uuid.uuid4())


def _aware(v: Any) -> datetime:
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


class MemoryStore(Store):
    def __init__(self, clock: Callable[[], datetime] | None = None):
        self.now: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))
        self._l = threading.RLock()
        self.workspaces: dict[str, Row] = {}
        self.memberships: dict[tuple[str, str], Row] = {}
        self.projects: dict[str, Row] = {}
        self.runtimes: dict[str, Row] = {}
        self.credentials: dict[str, Row] = {}
        self.runs: dict[str, Row] = {}
        self.conversations: dict[str, Row] = {}
        self.messages: list[Row] = []
        self.jobs: dict[str, Row] = {}
        self.events: dict[str, list[Row]] = {}
        self.gpu: list[Row] = []
        self.vllm: list[Row] = []
        self.batches: dict[tuple[str, str], datetime] = {}
        self.audit_log: list[Row] = []
        self._ordinal = 0

    # ------------------------------------------------------------ tenancy
    def create_workspace(self, user_id, name, slug):
        with self._l:
            if any(w["slug"] == slug for w in self.workspaces.values()):
                return {"error": "slug_taken"}
            if sum(1 for m in self.memberships.values() if m["user_id"] == user_id and m["role"] == "owner") >= 5:
                return {"error": "workspace_limit"}
            ws = {"id": _uid(), "name": name, "slug": slug, "created_by": user_id, "created_at": self.now()}
            self.workspaces[ws["id"]] = ws
            self.memberships[(ws["id"], user_id)] = {"workspace_id": ws["id"], "user_id": user_id, "role": "owner", "created_at": self.now()}
            pr = {"id": _uid(), "workspace_id": ws["id"], "name": "Default", "created_by": user_id, "created_at": self.now(), "archived_at": None}
            self.projects[pr["id"]] = pr
            self.audit(ws["id"], user_id, "workspace.create", "workspace", ws["id"])
            return {"ok": True, "workspace": copy.deepcopy(ws), "project": copy.deepcopy(pr)}

    def list_workspaces(self, user_id):
        with self._l:
            return [copy.deepcopy({**self.workspaces[w], "role": m["role"]}) for (w, u), m in self.memberships.items() if u == user_id]

    def get_role(self, user_id, workspace_id):
        m = self.memberships.get((workspace_id, user_id))
        return m["role"] if m else None

    def add_member(self, workspace_id: str, user_id: str, role: str) -> None:      # test/dev helper (invites are post-MVP)
        self.memberships[(workspace_id, user_id)] = {"workspace_id": workspace_id, "user_id": user_id, "role": role, "created_at": self.now()}

    def create_project(self, workspace_id, name, user_id):
        with self._l:
            p = {"id": _uid(), "workspace_id": workspace_id, "name": name, "created_by": user_id, "created_at": self.now(), "archived_at": None}
            self.projects[p["id"]] = p
            return copy.deepcopy(p)

    def list_projects(self, workspace_id):
        return [copy.deepcopy(p) for p in self.projects.values() if p["workspace_id"] == workspace_id and not p["archived_at"]]

    def get_project(self, project_id):
        return copy.deepcopy(self.projects.get(project_id))

    # ------------------------------------------------------------ runtimes
    def create_runtime(self, workspace_id, project_id, name, is_mock, user_id):
        with self._l:
            p = self.projects.get(project_id)
            if not p or p["workspace_id"] != workspace_id:
                raise ValueError("project_not_in_workspace")
            r = {"id": _uid(), "workspace_id": workspace_id, "project_id": project_id, "name": name, "is_mock": is_mock, "served_model": None,
                 "tp": None, "pp": None, "max_model_len": None, "capabilities": {}, "binary_identity": {}, "last_heartbeat_at": None,
                 "registered_at": None, "disabled_at": None, "created_by": user_id, "created_at": self.now()}
            self.runtimes[r["id"]] = r
            return copy.deepcopy(r)

    def get_runtime(self, runtime_id):
        return copy.deepcopy(self.runtimes.get(runtime_id))

    def list_runtimes(self, project_id):
        return [copy.deepcopy(r) for r in self.runtimes.values() if r["project_id"] == project_id and not r["disabled_at"]]

    def add_credential(self, runtime, prefix, secret_hash, expires_at, user_id, rotated_from=None):
        with self._l:
            if any(c["prefix"] == prefix for c in self.credentials.values()):
                raise ValueError("prefix_collision")
            c = {"id": _uid(), "workspace_id": runtime["workspace_id"], "project_id": runtime["project_id"], "runtime_id": runtime["id"], "prefix": prefix,
                 "secret_hash": secret_hash, "scopes": ["runtime:connect"], "created_by": user_id, "created_at": self.now(), "expires_at": expires_at,
                 "revoked_at": None, "rotated_from": rotated_from, "last_used_at": None}
            self.credentials[c["id"]] = c
            return copy.deepcopy(c)

    def get_credential_by_prefix(self, prefix):
        for c in self.credentials.values():
            if c["prefix"] == prefix:
                return copy.deepcopy(c)
        return None

    def revoke_credentials(self, runtime_id):
        with self._l:
            n = 0
            for c in self.credentials.values():
                if c["runtime_id"] == runtime_id and not c["revoked_at"]:
                    c["revoked_at"] = self.now(); n += 1
            return n

    def revoke_credential(self, credential_id):
        with self._l:
            c = self.credentials.get(credential_id)
            if c and not c["revoked_at"]:
                c["revoked_at"] = self.now()

    def list_credentials(self, runtime_id):
        return [{k: v for k, v in copy.deepcopy(c).items() if k != "secret_hash"} for c in self.credentials.values() if c["runtime_id"] == runtime_id]

    def touch_credential(self, credential_id):
        c = self.credentials.get(credential_id)
        if c:
            c["last_used_at"] = self.now()

    def disable_runtime(self, runtime_id):
        with self._l:
            if runtime_id in self.runtimes:
                self.runtimes[runtime_id]["disabled_at"] = self.now()

    def register_runtime(self, runtime_id, info):
        with self._l:
            r = self.runtimes[runtime_id]
            r.update(served_model=info["served_model"], tp=info["tp"], pp=info["pp"], max_model_len=info["max_model_len"],
                     capabilities={"gpus": info["gpus"], **info["capabilities"]}, binary_identity=info["sdk"],
                     registered_at=self.now(), last_heartbeat_at=self.now())
            return self._start_run(runtime_id, info)

    def _start_run(self, runtime_id, meta):
        rt = self.runtimes[runtime_id]
        for run in self.runs.values():
            if run["runtime_id"] == runtime_id and run["status"] == "active":
                run.update(status="aborted", ended_at=self.now())
        run = {"id": _uid(), "workspace_id": rt["workspace_id"], "project_id": rt["project_id"], "runtime_id": runtime_id, "kind": "session",
               "is_mock": rt["is_mock"], "model": meta.get("served_model"), "tp": meta.get("tp"), "pp": meta.get("pp"),
               "max_model_len": meta.get("max_model_len"), "status": "active", "started_at": self.now(), "ended_at": None, "summary": {}}
        self.runs[run["id"]] = run
        return run["id"]

    def heartbeat(self, runtime_id):
        with self._l:
            r = self.runtimes.get(runtime_id)
            if not r or r["disabled_at"]:
                return None
            r["last_heartbeat_at"] = self.now()
            return r["last_heartbeat_at"]

    # ------------------------------------------------------------ chat
    def create_conversation(self, workspace_id, project_id, user_id, title, runtime_id):
        with self._l:
            c = {"id": _uid(), "workspace_id": workspace_id, "project_id": project_id, "runtime_id": runtime_id, "title": title or "New conversation",
                 "created_by": user_id, "created_at": self.now(), "updated_at": self.now()}
            self.conversations[c["id"]] = c
            return copy.deepcopy(c)

    def get_conversation(self, conversation_id):
        return copy.deepcopy(self.conversations.get(conversation_id))

    def list_conversations(self, project_id, limit=50):
        rows = sorted((c for c in self.conversations.values() if c["project_id"] == project_id), key=lambda c: c["updated_at"], reverse=True)
        return copy.deepcopy(rows[:limit])

    def list_messages(self, conversation_id, limit=200):
        rows = [m for m in self.messages if m["conversation_id"] == conversation_id]
        return copy.deepcopy(sorted(rows, key=lambda m: m["ordinal"])[-limit:])

    def _add_message(self, workspace_id, conversation_id, role, content, job_id=None, meta=None) -> Row:
        self._ordinal += 1
        m = {"id": _uid(), "ordinal": self._ordinal, "workspace_id": workspace_id, "conversation_id": conversation_id, "role": role,
             "content": content, "job_id": job_id, "meta": meta or {}, "created_at": self.now()}
        self.messages.append(m)
        return m

    # ------------------------------------------------------------ jobs
    def submit_job(self, workspace_id, conversation_id, runtime_id, user_id, idem, content, params, char_budget):
        with self._l:
            conv = self.conversations.get(conversation_id)
            if not conv or conv["workspace_id"] != workspace_id:
                return {"error": "not_found"}
            rt = self.runtimes.get(runtime_id)
            if not rt or rt["workspace_id"] != workspace_id or rt["project_id"] != conv["project_id"] or rt["disabled_at"]:
                return {"error": "runtime_not_found"}
            existing = next((j for j in self.jobs.values() if j["workspace_id"] == workspace_id and j["idempotency_key"] == idem), None)
            if existing:
                if existing["conversation_id"] != conversation_id:
                    return {"error": "idempotency_conflict"}
                return {"ok": True, "job_id": existing["id"], "status": existing["status"], "idempotent": True}
            if len(content) > char_budget:
                return {"error": "prompt_too_long", "char_budget": char_budget}
            if sum(1 for j in self.jobs.values() if j["runtime_id"] == runtime_id and j["status"] in ACTIVE) >= CAPS["active_jobs_per_runtime"]:
                return {"error": "too_many_jobs"}
            if sum(1 for j in self.jobs.values() if j["requested_by"] == user_id and j["workspace_id"] == workspace_id and j["status"] in ACTIVE) >= CAPS["active_jobs_per_user"]:
                return {"error": "too_many_user_jobs"}
            msg = self._add_message(workspace_id, conversation_id, "user", content)
            hist, running = [], 0
            for m in sorted((m for m in self.messages if m["conversation_id"] == conversation_id and m["role"] in ("user", "assistant")),
                            key=lambda m: m["ordinal"], reverse=True):
                running += len(m["content"])
                if running > char_budget:
                    break
                hist.append({"role": m["role"], "content": m["content"]})
            hist.reverse()
            now = self.now()
            job = {"id": _uid(), "workspace_id": workspace_id, "project_id": conv["project_id"], "conversation_id": conversation_id, "runtime_id": runtime_id,
                   "requested_by": user_id, "idempotency_key": idem, "status": "pending",
                   "request": {"messages": hist, "max_tokens": int(params["max_tokens"]), "temperature": float(params["temperature"]), "model": rt["served_model"]},
                   "attempt": 0, "max_attempts": 2, "lease_owner": None, "lease_expires_at": None, "last_event_seq": 0, "event_count": 0, "text_chars": 0,
                   "created_at": now, "deadline_at": now + timedelta(minutes=10), "claimed_at": None, "first_token_at": None, "finished_at": None,
                   "cancel_requested_at": None, "error_code": None, "error_message": None, "result": None, "assistant_message_id": None}
            self.jobs[job["id"]] = job
            self.events[job["id"]] = []
            conv.update(updated_at=now, runtime_id=runtime_id)
            return {"ok": True, "job_id": job["id"], "status": "pending", "user_message_id": msg["id"]}

    def expire_jobs(self, runtime_id=None):
        with self._l:
            now = self.now()
            for j in self.jobs.values():
                if runtime_id and j["runtime_id"] != runtime_id:
                    continue
                if j["status"] == "pending" and j["deadline_at"] < now:
                    j.update(status="expired", finished_at=now, error_code="deadline", error_message="job was not completed before its deadline")
                elif j["status"] == "cancel_requested" and j["lease_expires_at"] and j["lease_expires_at"] < now:
                    j.update(status="cancelled", finished_at=now)
                elif j["status"] in ("claimed", "running") and j["lease_expires_at"] and j["lease_expires_at"] < now \
                        and (j["attempt"] >= j["max_attempts"] or j["deadline_at"] < now):
                    j.update(status="failed", finished_at=now, error_code="lease_expired", error_message="runtime stopped responding and no attempts remain")

    def claim_job(self, runtime_id, owner, lease_seconds):
        with self._l:
            rt = self.runtimes.get(runtime_id)
            if not rt or rt["disabled_at"]:
                return None
            self.expire_jobs(runtime_id)
            now = self.now()
            lease = min(max(lease_seconds, 5), 120)
            cands = [j for j in self.jobs.values() if j["runtime_id"] == runtime_id and j["deadline_at"] > now and (
                j["status"] == "pending" or (j["status"] in ("claimed", "running") and j["lease_expires_at"] < now and j["attempt"] < j["max_attempts"]))]
            if not cands:
                return None
            j = min(cands, key=lambda j: j["created_at"])
            j.update(status="claimed", lease_owner=owner, lease_expires_at=now + timedelta(seconds=lease), attempt=j["attempt"] + 1,
                     claimed_at=now, first_token_at=None)
            return copy.deepcopy(j)

    def _job_text(self, job_id: str) -> str:
        evs = self.events.get(job_id, [])
        last_reset = max((e["event_seq"] for e in evs if e["kind"] == "reset"), default=0)
        return "".join(e["text"] or "" for e in sorted(evs, key=lambda e: e["event_seq"]) if e["kind"] == "delta" and e["event_seq"] > last_reset)

    def _lease_ok(self, j: Row, owner: str) -> bool:
        return j["lease_owner"] == owner and j["lease_expires_at"] is not None and j["lease_expires_at"] >= self.now()

    def append_events(self, runtime_id, job_id, owner, events, lease_seconds):
        with self._l:
            j = self.jobs.get(job_id)
            if not j or j["runtime_id"] != runtime_id:
                return {"error": "not_found"}
            if j["status"] in TERMINAL:
                return {"error": "terminal", "status": j["status"]}
            if not self._lease_ok(j, owner):
                return {"error": "lease_lost"}
            lease = min(max(lease_seconds, 5), 120)
            last, accepted, dups, first_new, has_delta = j["last_event_seq"], 0, 0, None, False
            event_count, text_chars = j["event_count"], j["text_chars"]
            for e in sorted(events, key=lambda e: e["event_seq"]):
                seq = e["event_seq"]
                if seq <= last:
                    dups += 1
                    continue
                if seq > last + 1:
                    j.update(last_event_seq=last, event_count=event_count, text_chars=text_chars, lease_expires_at=self.now() + timedelta(seconds=lease))
                    return {"error": "gap", "expected_seq": last + 1, "last_event_seq": last, "accepted": accepted}
                n = len(e.get("text") or "")
                if event_count + 1 > CAPS["job_events"] or text_chars + n > CAPS["job_text_chars"]:
                    j.update(last_event_seq=last, event_count=event_count, status="failed", finished_at=self.now(), error_code="cap_exceeded",
                             error_message="job output exceeded the event or text cap")
                    return {"error": "cap_exceeded"}
                kind = e.get("kind", "delta")
                self.events[job_id].append({"job_id": job_id, "event_seq": seq, "workspace_id": j["workspace_id"], "kind": kind, "text": e.get("text"),
                                            "token_count": e.get("token_count"), "chunk_count": e.get("chunk_count"), "emitted_at": e.get("emitted_at"), "received_at": self.now()})
                event_count += 1
                if kind == "reset":
                    text_chars = 0
                if kind == "delta":
                    has_delta, text_chars = True, text_chars + n
                first_new = first_new or seq
                last, accepted = seq, accepted + 1
            was_cancel = j["status"] == "cancel_requested"
            j.update(last_event_seq=last, event_count=event_count, text_chars=text_chars,
                     status=j["status"] if was_cancel else "running", lease_expires_at=self.now() + timedelta(seconds=lease))
            if has_delta and not j["first_token_at"]:
                j["first_token_at"] = self.now()
            return {"ok": True, "last_event_seq": last, "accepted": accepted, "duplicates": dups, "first_new_seq": first_new, "cancel_requested": was_cancel}

    def complete_job(self, runtime_id, job_id, owner, final_seq, text_sha256, result):
        with self._l:
            j = self.jobs.get(job_id)
            if not j or j["runtime_id"] != runtime_id:
                return {"error": "not_found"}
            if j["status"] == "succeeded":
                if (j["result"] or {}).get("text_sha256") == text_sha256:
                    return {"ok": True, "idempotent": True, "assistant_message_id": j["assistant_message_id"]}
                return {"error": "conflict"}
            if j["status"] in TERMINAL:
                return {"error": "terminal", "status": j["status"]}
            if not self._lease_ok(j, owner):
                return {"error": "lease_lost"}
            if final_seq != j["last_event_seq"]:
                return {"error": "seq_mismatch", "expected_seq": j["last_event_seq"]}
            text = self._job_text(job_id)
            if hashlib.sha256(text.encode()).hexdigest() != text_sha256:
                return {"error": "hash_mismatch"}
            msg = self._add_message(j["workspace_id"], j["conversation_id"], "assistant", text, job_id, {"measured": result or {}, "runtime_id": runtime_id})
            j.update(status="succeeded", finished_at=self.now(), assistant_message_id=msg["id"], result={**(result or {}), "text_sha256": text_sha256}, lease_expires_at=None)
            self.conversations[j["conversation_id"]]["updated_at"] = self.now()
            return {"ok": True, "assistant_message_id": msg["id"]}

    def fail_job(self, runtime_id, job_id, owner, code, message, retryable):
        with self._l:
            j = self.jobs.get(job_id)
            if not j or j["runtime_id"] != runtime_id:
                return {"error": "not_found"}
            if j["status"] in TERMINAL:
                return {"ok": True, "idempotent": True, "status": j["status"]}
            if not self._lease_ok(j, owner):
                return {"error": "lease_lost"}
            if retryable and j["attempt"] < j["max_attempts"] and j["status"] != "cancel_requested":
                j.update(status="pending", lease_owner=None, lease_expires_at=None, error_code=code[:60], error_message=message[:500])
                return {"ok": True, "status": "pending", "requeued": True}
            j.update(status="cancelled" if j["status"] == "cancel_requested" else "failed", finished_at=self.now(), error_code=code[:60],
                     error_message=message[:500], lease_expires_at=None)
            return {"ok": True, "status": "failed"}

    def ack_cancel(self, runtime_id, job_id, owner):
        with self._l:
            j = self.jobs.get(job_id)
            if not j or j["runtime_id"] != runtime_id:
                return {"error": "not_found"}
            if j["status"] in TERMINAL:
                return {"ok": True, "idempotent": True, "status": j["status"]}
            if not self._lease_ok(j, owner):
                return {"error": "lease_lost"}
            if j["status"] != "cancel_requested":
                return {"error": "not_cancel_requested", "status": j["status"]}
            text = self._job_text(job_id)
            if text:
                self._add_message(j["workspace_id"], j["conversation_id"], "assistant", text, job_id, {"cancelled": True})
            j.update(status="cancelled", finished_at=self.now(), lease_expires_at=None)
            return {"ok": True, "status": "cancelled"}

    def request_cancel(self, workspace_id, job_id):
        with self._l:
            j = self.jobs.get(job_id)
            if not j or j["workspace_id"] != workspace_id:
                return {"error": "not_found"}
            if j["status"] == "pending":
                j.update(status="cancelled", finished_at=self.now(), cancel_requested_at=self.now())
                return {"ok": True, "status": "cancelled"}
            if j["status"] in ("claimed", "running"):
                j.update(status="cancel_requested", cancel_requested_at=self.now())
                return {"ok": True, "status": "cancel_requested"}
            return {"ok": True, "status": j["status"], "idempotent": True}

    def get_job(self, job_id):
        with self._l:
            self.expire_jobs()
            return copy.deepcopy(self.jobs.get(job_id))

    def list_events(self, job_id, after_seq, limit):
        with self._l:
            return copy.deepcopy([e for e in sorted(self.events.get(job_id, []), key=lambda e: e["event_seq"]) if e["event_seq"] > after_seq][:limit])

    # ------------------------------------------------------------ telemetry
    def ingest_telemetry(self, runtime_id, batch_id, run_id, gpu, vllm):
        with self._l:
            rt = self.runtimes.get(runtime_id)
            if not rt or rt["disabled_at"]:
                return {"error": "not_found"}
            if len(gpu) > CAPS["gpu_rows"] or len(vllm) > CAPS["vllm_rows"]:
                return {"error": "batch_too_large"}
            if run_id and (run_id not in self.runs or self.runs[run_id]["runtime_id"] != runtime_id):
                return {"error": "run_not_found"}
            if (runtime_id, batch_id) in self.batches:
                return {"ok": True, "duplicate": True}
            self.batches[(runtime_id, batch_id)] = self.now()
            seen_g = {(g["runtime_id"], g["gpu_index"], g["captured_at"]) for g in self.gpu if g["runtime_id"] == runtime_id}
            ng = 0
            for g in gpu:
                key = (runtime_id, g["gpu_index"], _aware(g["captured_at"]))
                if key in seen_g:
                    continue
                seen_g.add(key)
                self.gpu.append({**g, "workspace_id": rt["workspace_id"], "project_id": rt["project_id"], "runtime_id": runtime_id, "run_id": run_id,
                                 "captured_at": _aware(g["captured_at"]), "is_mock": rt["is_mock"]})
                ng += 1
            seen_v = {v["captured_at"] for v in self.vllm if v["runtime_id"] == runtime_id}
            nv = 0
            for v in vllm:
                ts = _aware(v["captured_at"])
                if ts in seen_v:
                    continue
                seen_v.add(ts)
                self.vllm.append({**v, "workspace_id": rt["workspace_id"], "project_id": rt["project_id"], "runtime_id": runtime_id, "run_id": run_id,
                                  "captured_at": ts, "is_mock": rt["is_mock"]})
                nv += 1
            return {"ok": True, "gpu_rows": ng, "vllm_rows": nv}

    def _query(self, rows, project_id, runtime_id, since, until, limit):
        out = [r for r in rows if r["project_id"] == project_id and (runtime_id is None or r["runtime_id"] == runtime_id) and since <= r["captured_at"] <= until]
        return copy.deepcopy(sorted(out, key=lambda r: r["captured_at"])[:limit])

    def query_gpu(self, project_id, runtime_id, since, until, limit):
        return self._query(self.gpu, project_id, runtime_id, since, until, limit)

    def query_vllm(self, project_id, runtime_id, since, until, limit):
        return self._query(self.vllm, project_id, runtime_id, since, until, limit)

    # ------------------------------------------------------------ runs / housekeeping
    def reap_runs(self, stale_seconds=90):
        with self._l:
            n = 0
            cutoff = self.now() - timedelta(seconds=stale_seconds)
            for run in self.runs.values():
                rt = self.runtimes[run["runtime_id"]]
                if run["status"] == "active" and (rt["last_heartbeat_at"] is None or rt["last_heartbeat_at"] < cutoff):
                    run.update(status="aborted", ended_at=rt["last_heartbeat_at"] or self.now()); n += 1
            return n

    def list_runs(self, project_id, limit=50):
        self.reap_runs()
        rows = sorted((r for r in self.runs.values() if r["project_id"] == project_id), key=lambda r: r["started_at"], reverse=True)
        return copy.deepcopy(rows[:limit])

    def get_run(self, run_id):
        return copy.deepcopy(self.runs.get(run_id))

    def prune(self, event_hours=48, gpu_days=14, vllm_days=14):
        with self._l:
            now = self.now()
            ne = 0
            for jid, evs in self.events.items():
                if self.jobs[jid]["status"] in TERMINAL:
                    keep = [e for e in evs if e["received_at"] >= now - timedelta(hours=event_hours)]
                    ne += len(evs) - len(keep)
                    self.events[jid] = keep
            g0, v0 = len(self.gpu), len(self.vllm)
            self.gpu = [g for g in self.gpu if g["captured_at"] >= now - timedelta(days=gpu_days)]
            self.vllm = [v for v in self.vllm if v["captured_at"] >= now - timedelta(days=vllm_days)]
            b0 = len(self.batches)
            self.batches = {k: t for k, t in self.batches.items() if t >= now - timedelta(days=2)}
            return {"events": ne, "gpu_samples": g0 - len(self.gpu), "vllm_samples": v0 - len(self.vllm), "batches": b0 - len(self.batches)}

    def audit(self, workspace_id, actor, action, target_type=None, target_id=None, meta=None):
        self.audit_log.append({"workspace_id": workspace_id, "actor": actor, "action": action, "target_type": target_type, "target_id": target_id,
                               "meta": meta or {}, "at": self.now()})
