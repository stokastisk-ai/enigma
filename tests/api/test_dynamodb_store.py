"""DynamoDB (Track B) parity gate, run against moto -- an in-process emulator. NO AWS ACCOUNT, NETWORK OR CREDENTIAL IS USED.

Safety: every test runs with FAKE credentials, no profile/config files and ``mock_aws`` active (asserted), so even a mocking failure could not
reach a real account. Results are emulator evidence only: moto is not a faithful model of DynamoDB throttling, partition limits or real
concurrency, so this does NOT prove production behaviour. See docs/AWS_DYNAMODB_REAL_PARITY.md.

1. The existing store-agnostic API suite (jobs, leases, tenancy, telemetry, route guards) is re-run against DynamoStore with the same fake
   clock as the in-memory store, so lease expiry / deadlines / staleness are exercised too.
2. DynamoDB-specific tests: no GSIs and strongly consistent reads only, atomic claim race, ordered/deduplicated events under concurrency,
   submit caps under a race, idempotent submit, failure injection, retention, durability, isolation, secret hygiene, row-shape parity with the
   in-memory reference.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from moto import mock_aws

from api_helpers import DEV_SECRET, Clock, Tenant, sha
from enigma_api.main import create_app
from enigma_api.realtime import RecordingBroadcaster
from enigma_api.settings import Settings
from enigma_api.store.dynamodb import DynamoStore
from enigma_api.store.memory import MemoryStore
from enigma_api.store.base import StoreUnavailable

# OPTIONAL real-table parity run (OFF by default; needs owner approval + a NON-ROOT identity + a throwaway table; see docs/AWS_DYNAMODB_REAL_PARITY.md).
# When ENIGMA_LIVE_DDB=1 the SAME tests run against the real table named in ENIGMA_LIVE_DDB_TABLE instead of moto, after tests/live_ddb_guard.py has proven
# that the target is a throwaway `enigma-parity-*` table, the caller is not root and the region is the approved one. Never part of the default suite.
LIVE = os.environ.get("ENIGMA_LIVE_DDB") == "1"
TABLE = os.environ.get("ENIGMA_LIVE_DDB_TABLE", "") if LIVE else "enigma-test"


class SpyClient:
    """Delegates to the real (moto) client and records every call, so tests can assert consistency and inject faults."""

    def __init__(self, inner):
        self._i, self.calls, self.fail = inner, [], {}
        # Real DynamoDB makes each API call (including TransactWriteItems) atomic. moto does NOT lock its tables, so concurrent calls crash the
        # emulator itself ("dictionary changed size during iteration"). Serialising individual calls models the real per-call atomicity;
        # threads still interleave BETWEEN calls, which is exactly the read-modify-write logic these race tests exercise.
        self._lock = threading.RLock()

    def __getattr__(self, name):
        fn = getattr(self._i, name)
        if not callable(fn):
            return fn

        def wrapped(*a, **kw):
            with self._lock:
                self.calls.append((name, kw))
                rule = self.fail.get(name)
                if rule and rule["left"] != 0:
                    if rule["left"] > 0:
                        rule["left"] -= 1
                    raise ClientError({"Error": {"Code": rule["code"], "Message": "injected"}, **rule.get("extra", {})}, name)
                return fn(*a, **kw)
        return wrapped


def _clear_table(c) -> None:
    """Delete every item of the (guard-approved, throwaway) table so each test starts empty. Only ever called after assert_safe_live_target."""
    start = None
    while True:
        r = c.scan(TableName=TABLE, ConsistentRead=True, ProjectionExpression="PK, SK", **({"ExclusiveStartKey": start} if start else {}))
        items = r.get("Items", [])
        for i in range(0, len(items), 25):
            c.batch_write_item(RequestItems={TABLE: [{"DeleteRequest": {"Key": {"PK": it["PK"], "SK": it["SK"]}}} for it in items[i:i + 25]]})
        start = r.get("LastEvaluatedKey")
        if not start:
            return


def _ddb_live():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))        # tests/ (holds live_ddb_guard.py)
    from live_ddb_guard import LiveTargetRefused, assert_safe_live_target
    try:
        profile, region = os.environ["ENIGMA_LIVE_DDB_PROFILE"], os.environ["ENIGMA_LIVE_DDB_REGION"]
    except KeyError as e:
        pytest.fail(f"live mode needs {e.args[0]}")
    sess = boto3.Session(profile_name=profile, region_name=region)
    arn = sess.client("sts").get_caller_identity()["Arn"]
    c = sess.client("dynamodb")
    try:
        d = c.describe_table(TableName=TABLE)["Table"]
        tags = {t["Key"]: t["Value"] for t in c.list_tags_of_resource(ResourceArn=d["TableArn"]).get("Tags", [])}
        assert_safe_live_target(identity_arn=arn, table_name=TABLE, table_status=d["TableStatus"], item_count=d.get("ItemCount"), tags=tags, region=region,
                                approved_region=os.environ.get("ENIGMA_LIVE_DDB_APPROVED_REGION"), env=dict(os.environ))
    except LiveTargetRefused as e:
        pytest.fail(str(e))
    _clear_table(c)
    yield c


@pytest.fixture
def ddb(monkeypatch):
    if LIVE:
        yield from _ddb_live()
    else:
        yield from _ddb_moto(monkeypatch)


def _ddb_moto(monkeypatch):
    for k in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_ENDPOINT_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    with mock_aws():
        c = boto3.client("dynamodb", region_name="us-east-1")
        key = c._request_signer._credentials.access_key                  # moto installs its own fake key; env creds are fake too
        assert key in ("testing", "FOOBARKEY") and not key.startswith(("AKIA", "ASIA")), "refusing to run: not using fake credentials"
        c.create_table(TableName=TABLE, BillingMode="PAY_PER_REQUEST",
                       AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"}, {"AttributeName": "SK", "AttributeType": "S"}],
                       KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}])
        yield c


@pytest.fixture
def clock():
    c = Clock()
    if LIVE:                                                    # a real table's TTL must not see "past" expiry times
        from datetime import datetime, timezone
        c.t = datetime.now(timezone.utc)
    return c


@pytest.fixture
def spy(ddb):
    return SpyClient(ddb)


@pytest.fixture
def store(spy, clock):
    return DynamoStore(TABLE, client=spy, clock=clock)


@pytest.fixture
def bus():
    return RecordingBroadcaster()


@pytest.fixture
def client(store, bus, clock):
    s = Settings(env="test", dev_auth=True, dev_jwt_secret=DEV_SECRET, cron_secret="c" * 32)
    with TestClient(create_app(s, store=store, broadcaster=bus, clock=clock), raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------- 1. the existing behavioural suite, run on DynamoStore
def _import_suite() -> None:
    import test_auth_and_tenancy, test_jobs, test_route_guards, test_telemetry    # noqa: E401
    skip_args = {"monkeypatch", "kwargs", "expect"}                                # settings-only tests: store independent, already run once
    skip_names = {"test_token_hash_only_stored_and_never_listed"}                  # reaches into MemoryStore internals; replaced below
    n = 0
    for mod in (test_jobs, test_telemetry, test_auth_and_tenancy, test_route_guards):
        for name, fn in vars(mod).items():
            if name.startswith("test_") and inspect.isfunction(fn) and name not in skip_names and not (set(inspect.signature(fn).parameters) & skip_args):
                globals()[f"test_ddb_{mod.__name__.removeprefix('test_')}_{name.removeprefix('test_')}"] = fn
                n += 1
    globals()["_IMPORTED"] = n


_import_suite()


def test_imported_suite_is_substantial():
    assert _IMPORTED >= 40


# ---------------------------------------------------------------- 2. DynamoDB-specific
def test_no_gsi_and_every_read_is_strongly_consistent(ddb, spy, client):
    t = ddb.describe_table(TableName=TABLE)["Table"]
    assert not t.get("GlobalSecondaryIndexes") and not t.get("LocalSecondaryIndexes")
    a = Tenant(client, "user_alice")
    a.create_runtime(); a.register()
    conv = a.conversation()
    job_id = a.submit(conv, "hi").json()["job_id"]
    a.claim(); a.events(job_id, [{"event_seq": 1, "kind": "delta", "text": "x"}])
    client.get(f"/api/v1/app/jobs/{job_id}", headers=a.h)
    client.get(f"/api/v1/app/projects/{a.project_id}/metrics/gpu", headers=a.h)
    reads = [(n, kw) for n, kw in spy.calls if n in ("get_item", "query", "scan")]
    assert reads and all(kw.get("ConsistentRead") is True for _, kw in reads)
    for n, kw in spy.calls:
        if n == "batch_get_item":
            assert all(v.get("ConsistentRead") for v in kw["RequestItems"].values())


def test_atomic_claim_exactly_one_winner_under_a_thread_race(spy, clock):
    ddb = spy
    st = DynamoStore(TABLE, client=ddb, clock=clock)
    ws = st.create_workspace("u1", "W", "w")["workspace"]
    pr = st.list_projects(ws["id"])[0]
    rt = st.create_runtime(ws["id"], pr["id"], "rt", True, "u1")
    cv = st.create_conversation(ws["id"], pr["id"], "u1", "c", rt["id"])
    assert st.submit_job(ws["id"], cv["id"], rt["id"], "u1", "k-race-1", "go", {"max_tokens": 8, "temperature": 0.5}, 1000)["ok"]
    wins, errs = [], []

    def worker(i):
        try:
            wins.append(DynamoStore(TABLE, client=ddb, clock=clock).claim_job(rt["id"], f"w{i}", 30))
        except Exception as e:                                           # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs and sum(1 for w in wins if w) == 1
    j = st.get_job(next(w for w in wins if w)["id"])
    assert j["attempt"] == 1 and j["status"] == "claimed"


def test_concurrent_event_appends_stay_ordered_without_duplicates(spy, clock):
    ddb = spy
    st = DynamoStore(TABLE, client=ddb, clock=clock)
    ws = st.create_workspace("u1", "W", "w")["workspace"]
    pr = st.list_projects(ws["id"])[0]
    rt = st.create_runtime(ws["id"], pr["id"], "rt", True, "u1")
    cv = st.create_conversation(ws["id"], pr["id"], "u1", "c", rt["id"])
    st.submit_job(ws["id"], cv["id"], rt["id"], "u1", "k-ord-1", "go", {"max_tokens": 8, "temperature": 0.5}, 1000)
    job = st.claim_job(rt["id"], "owner", 60)
    batch = [{"event_seq": i, "kind": "delta", "text": f"{i};"} for i in range(1, 21)]
    res = []

    def worker():
        res.append(DynamoStore(TABLE, client=ddb, clock=clock).append_events(rt["id"], job["id"], "owner", batch, 60))

    ts = [threading.Thread(target=worker) for _ in range(4)]               # four racing replays of the same batch
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(r.get("accepted", 0) for r in res) == 20                    # each event accepted exactly once
    assert sum(r.get("duplicates", 0) for r in res) + 20 == sum(r.get("accepted", 0) + r.get("duplicates", 0) for r in res)
    evs = st.list_events(job["id"], 0, 100)
    assert [e["event_seq"] for e in evs] == list(range(1, 21))
    assert st.get_job(job["id"])["last_event_seq"] == 20


def test_submit_caps_hold_under_a_race(spy, clock):
    ddb = spy
    st = DynamoStore(TABLE, client=ddb, clock=clock)
    ws = st.create_workspace("u1", "W", "w")["workspace"]
    pr = st.list_projects(ws["id"])[0]
    rt = st.create_runtime(ws["id"], pr["id"], "rt", True, "u1")
    cv = st.create_conversation(ws["id"], pr["id"], "u1", "c", rt["id"])
    out = []

    def worker(i):
        out.append(DynamoStore(TABLE, client=ddb, clock=clock).submit_job(ws["id"], cv["id"], rt["id"], "u1", f"key-race-{i:04d}", "p", {"max_tokens": 8, "temperature": 0.1}, 1000))

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(1 for r in out if r.get("ok")) == 2                         # active_jobs_per_user == 2, never 3
    assert all(r.get("ok") or r.get("error") in ("too_many_user_jobs", "too_many_jobs") for r in out)


def test_concurrent_identical_idempotency_key_creates_one_job(spy, clock):
    ddb = spy
    st = DynamoStore(TABLE, client=ddb, clock=clock)
    ws = st.create_workspace("u1", "W", "w")["workspace"]
    pr = st.list_projects(ws["id"])[0]
    rt = st.create_runtime(ws["id"], pr["id"], "rt", True, "u1")
    cv = st.create_conversation(ws["id"], pr["id"], "u1", "c", rt["id"])
    out = []

    def worker():
        out.append(DynamoStore(TABLE, client=ddb, clock=clock).submit_job(ws["id"], cv["id"], rt["id"], "u1", "same-key-123456", "hello", {"max_tokens": 8, "temperature": 0.1}, 1000))

    ts = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len({r["job_id"] for r in out if r.get("ok")}) == 1
    assert len([m for m in st.list_messages(cv["id"]) if m["role"] == "user"]) == 1   # the user message was not duplicated


def test_transient_throttling_is_retried_and_permanent_failure_is_503_without_data(spy, store, client):
    a = Tenant(client, "user_alice")
    spy.fail["query"] = {"code": "ProvisionedThroughputExceededException", "left": 2}
    assert client.get("/api/v1/app/workspaces", headers=a.h).status_code == 200              # retried transparently
    spy.fail["get_item"] = {"code": "InternalServerError", "left": -1}
    spy.fail["query"] = {"code": "InternalServerError", "left": -1}
    with pytest.raises(StoreUnavailable) as e:
        store.get_role("secret-user", "ws")
    assert "secret-user" not in str(e.value) and "InternalServerError" in str(e.value)
    assert client.get("/api/v1/app/workspaces", headers=a.h).status_code == 503


def test_failed_completion_transaction_is_atomic_and_retry_finishes_exactly_once(spy, store, client):
    a = Tenant(client, "user_alice")
    a.create_runtime(); a.register()
    conv = a.conversation()
    job_id = a.submit(conv, "hi").json()["job_id"]
    a.claim(); a.events(job_id, [{"event_seq": 1, "kind": "delta", "text": "ok"}])
    spy.fail["transact_write_items"] = {"code": "InternalServerError", "left": -1}
    r = client.post(f"/api/v1/jobs/{job_id}/complete", headers=a.rh, json={"final_seq": 1, "text_sha256": sha("ok")})
    assert r.status_code == 503
    assert store.get_job(job_id)["status"] == "running"                                       # nothing half-applied
    assert [m["role"] for m in store.list_messages(conv)] == ["user"]
    spy.fail.clear()
    assert client.post(f"/api/v1/jobs/{job_id}/complete", headers=a.rh, json={"final_seq": 1, "text_sha256": sha("ok")}).status_code == 200
    assert [m["role"] for m in store.list_messages(conv)] == ["user", "assistant"]           # exactly one assistant message


def test_telemetry_partial_write_is_safe_to_replay(spy, store, client, clock):
    a = Tenant(client, "user_alice")
    a.create_runtime(); a.register()
    rows = [{"captured_at": (clock() + timedelta(seconds=i)).isoformat(), "gpu_index": i % 2, "util_gpu_pct": 10.0 + i} for i in range(30)]
    spy.fail["batch_write_item"] = {"code": "InternalServerError", "left": -1}
    with pytest.raises(StoreUnavailable):
        store.ingest_telemetry(a.runtime_id, "batch-partial-1", a.run_id, rows, [])
    spy.fail.clear()
    assert store.ingest_telemetry(a.runtime_id, "batch-partial-1", a.run_id, rows, []) == {"ok": True, "gpu_rows": 30, "vllm_rows": 0}
    assert store.ingest_telemetry(a.runtime_id, "batch-partial-1", a.run_id, rows, []) == {"ok": True, "duplicate": True}
    got = store.query_gpu(a.project_id, a.runtime_id, clock() - timedelta(minutes=1), clock() + timedelta(minutes=5), 1000)
    assert len(got) == 30 and [g["captured_at"] for g in got] == sorted(g["captured_at"] for g in got)


def test_tenant_isolation_at_the_store_level(store):
    a = store.create_workspace("alice", "A", "alpha")
    b = store.create_workspace("bob", "B", "beta")
    wa, wb = a["workspace"]["id"], b["workspace"]["id"]
    pa, pb = a["project"]["id"], b["project"]["id"]
    ra = store.create_runtime(wa, pa, "ra", True, "alice")
    rb = store.create_runtime(wb, pb, "rb", True, "bob")
    ca = store.create_conversation(wa, pa, "alice", "ca", ra["id"])
    assert store.get_role("bob", wa) is None and store.get_role("alice", wb) is None
    assert [w["id"] for w in store.list_workspaces("alice")] == [wa]
    assert [p["id"] for p in store.list_projects(wb)] == [pb]
    assert [r["id"] for r in store.list_runtimes(pb)] == [rb["id"]]
    assert store.submit_job(wb, ca["id"], rb["id"], "bob", "x-key-123456", "p", {"max_tokens": 4, "temperature": 0.1}, 100) == {"error": "not_found"}
    assert store.submit_job(wa, ca["id"], rb["id"], "alice", "y-key-123456", "p", {"max_tokens": 4, "temperature": 0.1}, 100) == {"error": "runtime_not_found"}
    ok = store.submit_job(wa, ca["id"], ra["id"], "alice", "z-key-123456", "p", {"max_tokens": 4, "temperature": 0.1}, 100)
    assert store.request_cancel(wb, ok["job_id"]) == {"error": "not_found"}                  # another workspace cannot cancel it
    assert store.claim_job(rb["id"], "w", 30) is None                                        # another runtime cannot claim it
    assert store.append_events(rb["id"], ok["job_id"], "w", [{"event_seq": 1, "text": "x"}], 30) == {"error": "not_found"}
    assert store.create_workspace("carol", "A2", "alpha") == {"error": "slug_taken"}         # slugs are globally unique


def test_owner_workspace_cap_is_atomic(store):
    for i in range(5):
        assert store.create_workspace("dave", f"W{i}", f"slug-{i}")["ok"]
    assert store.create_workspace("dave", "W6", "slug-6") == {"error": "workspace_limit"}
    assert store.create_workspace("erin", "W7", "slug-7")["ok"]                              # per-user, not global


def test_retention_prune_is_exact_and_spares_live_jobs(store, clock, client):
    a = Tenant(client, "user_alice")
    a.create_runtime(); a.register()
    conv = a.conversation()
    done = a.submit(conv, "one").json()["job_id"]
    a.claim(); a.events(done, [{"event_seq": 1, "kind": "delta", "text": "x"}])
    client.post(f"/api/v1/jobs/{done}/complete", headers=a.rh, json={"final_seq": 1, "text_sha256": sha("x")})
    live = a.submit(conv, "two").json()["job_id"]
    a.claim(); a.events(live, [{"event_seq": 1, "kind": "delta", "text": "y"}])
    store.ingest_telemetry(a.runtime_id, "old-batch-0001", a.run_id, [{"captured_at": clock().isoformat(), "gpu_index": 0, "util_gpu_pct": 1.0}], [])
    clock.advance(3 * 86400)                                                                  # past 48 h (events, batch marker) but inside 14 d (gpu rows)
    res = store.prune()
    assert res == {"events": 1, "gpu_samples": 0, "vllm_samples": 0, "batches": 1}            # nothing is deleted before its retention window ends
    assert [e["event_seq"] for e in store.list_events(live, 0, 10)] == [1]                   # a live (non-terminal) job's events survive
    assert store.list_events(done, 0, 10) == []                                              # the finished job's events are gone
    clock.advance(12 * 86400)                                                                 # now past 14 d
    assert store.prune()["gpu_samples"] == 1
    assert store.prune() == {"events": 0, "gpu_samples": 0, "vllm_samples": 0, "batches": 0}  # idempotent


def test_ttl_attribute_is_set_for_dynamodb_expiry(ddb, store, client, clock):
    a = Tenant(client, "user_alice")
    a.create_runtime(); a.register()
    conv = a.conversation()
    job = a.submit(conv, "p").json()["job_id"]
    a.claim(); a.events(job, [{"event_seq": 1, "kind": "delta", "text": "x"}])
    item = ddb.get_item(TableName=TABLE, Key={"PK": {"S": f"JOB#{job}"}, "SK": {"S": "EVT#0000000001"}}, ConsistentRead=True)["Item"]
    assert int(item["ttl"]["N"]) == int(clock().timestamp()) + 48 * 3600


def test_data_is_durable_across_store_instances_and_offline_history_remains(ddb, clock, client, store):
    a = Tenant(client, "user_alice")
    a.create_runtime(); a.register()
    conv = a.conversation()
    job = a.submit(conv, "remember me").json()["job_id"]
    a.claim(); a.events(job, [{"event_seq": 1, "kind": "delta", "text": "yes"}])
    client.post(f"/api/v1/jobs/{job}/complete", headers=a.rh, json={"final_seq": 1, "text_sha256": sha("yes")})
    clock.advance(600)                                                                          # connector gone for 10 minutes: runtime is offline
    fresh = DynamoStore(TABLE, client=ddb, clock=clock)                                         # new process / cold Lambda
    assert [(m["role"], m["content"]) for m in fresh.list_messages(conv)] == [("user", "remember me"), ("assistant", "yes")]
    assert fresh.get_job(job)["status"] == "succeeded"
    assert client.get(f"/api/v1/app/projects/{a.project_id}/overview", headers=a.h).json()["runtimes"][0]["status"] == "offline"


def test_credential_secret_hash_is_never_listed_and_plain_secret_never_stored(ddb, client):
    a = Tenant(client, "user_alice")
    a.create_runtime()
    secret = "_".join(a.token.split("_")[3:])                         # enig_rt_<prefix>_<43-char secret>; the secret is url-safe base64 and MAY contain "_"
    assert len(secret) == 43
    listed = client.get(f"/api/v1/app/runtimes/{a.runtime_id}/credentials", headers=a.h).text
    assert secret not in listed and "secret_hash" not in listed
    blob = str(ddb.scan(TableName=TABLE, ConsistentRead=True)["Items"])
    assert secret not in blob and a.token not in blob and hashlib.sha256(secret.encode()).hexdigest() in blob           # only the hash is persisted


def test_batchget_chunking_beyond_100_keys(store):
    w = store.create_workspace("alice", "A", "alpha")
    wid = w["workspace"]["id"]
    for i in range(130):
        store.create_project(wid, f"p{i}", "alice")
    assert len(store.list_projects(wid)) == 131


def test_event_cap_fails_the_job_and_keeps_accepted_events(store, monkeypatch):
    from enigma_api.store import dynamodb as d
    monkeypatch.setitem(d.CAPS, "job_events", 3)
    ws = store.create_workspace("u1", "W", "w")["workspace"]
    pr = store.list_projects(ws["id"])[0]
    rt = store.create_runtime(ws["id"], pr["id"], "rt", True, "u1")
    cv = store.create_conversation(ws["id"], pr["id"], "u1", "c", rt["id"])
    store.submit_job(ws["id"], cv["id"], rt["id"], "u1", "k-cap-12345", "go", {"max_tokens": 8, "temperature": 0.1}, 1000)
    job = store.claim_job(rt["id"], "o", 60)
    assert store.append_events(rt["id"], job["id"], "o", [{"event_seq": i, "kind": "delta", "text": "a"} for i in range(1, 6)], 60) == {"error": "cap_exceeded"}
    j = store.get_job(job["id"])
    assert j["status"] == "failed" and j["error_code"] == "cap_exceeded" and j["last_event_seq"] == 3
    assert store.claim_job(rt["id"], "o2", 60) is None                                           # terminal: pointer removed, never claimable


def test_dangling_queue_pointers_do_not_block_claims_or_caps(ddb, store):
    ws = store.create_workspace("u1", "W", "w")["workspace"]
    pr = store.list_projects(ws["id"])[0]
    rt = store.create_runtime(ws["id"], pr["id"], "rt", True, "u1")
    ddb.put_item(TableName=TABLE, Item={"PK": {"S": f"RTQ#{rt['id']}"}, "SK": {"S": "2026-01-01T00:00:00.000000Z#ghost"}, "ref": {"S": "ghost"}})
    assert store.claim_job(rt["id"], "o", 30) is None
    cv = store.create_conversation(ws["id"], pr["id"], "u1", "c", rt["id"])
    assert store.submit_job(ws["id"], cv["id"], rt["id"], "u1", "k-ghost-1234", "go", {"max_tokens": 8, "temperature": 0.1}, 1000)["ok"]


def test_row_shapes_match_the_inmemory_reference(store, clock):
    """Same scenario on MemoryStore and DynamoStore: identical key sets for every row type the API returns."""
    def scenario(st):
        w = st.create_workspace("u1", "W", "w-shape")
        ws, pr = w["workspace"], w["project"]
        rt = st.create_runtime(ws["id"], pr["id"], "rt", True, "u1")
        st.register_runtime(rt["id"], {"served_model": "m", "tp": 1, "pp": 1, "max_model_len": 2048, "gpus": [], "capabilities": {}, "sdk": {}})
        cv = st.create_conversation(ws["id"], pr["id"], "u1", "t", rt["id"])
        j = st.submit_job(ws["id"], cv["id"], rt["id"], "u1", "k-shape-1234", "hello", {"max_tokens": 8, "temperature": 0.5}, 1000)
        job = st.claim_job(rt["id"], "o", 30)
        st.append_events(rt["id"], job["id"], "o", [{"event_seq": 1, "kind": "delta", "text": "hi"}], 30)
        st.complete_job(rt["id"], job["id"], "o", 1, sha("hi"), {"ttft_ms": 1.5})
        return {"workspace": ws, "project": pr, "runtime": st.get_runtime(rt["id"]), "conversation": st.get_conversation(cv["id"]), "job": st.get_job(job["id"]),
                "messages": st.list_messages(cv["id"])[0], "events": st.list_events(job["id"], 0, 5)[0], "cred": None, "submit": j,
                "runs": st.list_runs(pr["id"])[0]}
    mem, dyn = scenario(MemoryStore(clock)), scenario(store)
    for k in mem:
        if mem[k] is None:
            continue
        assert set(mem[k]) == set(dyn[k]), (k, set(mem[k]) ^ set(dyn[k]))
    assert isinstance(dyn["job"]["created_at"], type(mem["job"]["created_at"])) and dyn["job"]["request"]["temperature"] == 0.5


def test_gpu_window_query_inclusive_bounds_and_runtime_filter(store, clock, client):
    a = Tenant(client, "user_alice")
    a.create_runtime(); a.register()
    t0 = clock()
    rows = [{"captured_at": (t0 + timedelta(seconds=i)).isoformat(), "gpu_index": 0, "util_gpu_pct": float(i)} for i in range(10)]
    store.ingest_telemetry(a.runtime_id, "win-batch-0001", a.run_id, rows, [])
    got = store.query_gpu(a.project_id, a.runtime_id, t0 + timedelta(seconds=2), t0 + timedelta(seconds=5), 100)
    assert [g["util_gpu_pct"] for g in got] == [2.0, 3.0, 4.0, 5.0]                              # both bounds inclusive, ascending
    assert store.query_gpu(a.project_id, "other-runtime", t0, t0 + timedelta(seconds=9), 100) == []
    assert len(store.query_gpu(a.project_id, None, t0, t0 + timedelta(seconds=9), 3)) == 3


def test_all_dynamodb_calls_target_the_configured_table_only(spy, client):
    a = Tenant(client, "user_alice")
    a.create_runtime(); a.register()
    for name, kw in spy.calls:
        tables = {kw.get("TableName")} if "TableName" in kw else set()
        for key in ("RequestItems",):
            tables |= set(kw.get(key, {}))
        for it in kw.get("TransactItems", []):
            tables |= {next(iter(it.values()))["TableName"]}
        assert tables <= {TABLE}, (name, tables)
    assert not [n for n, _ in spy.calls if n in ("create_table", "delete_table", "update_table", "scan")]   # data plane only, never schema ops


# ---------------------------------------------------------------- app wiring
def test_settings_select_dynamodb_and_production_requires_a_table():
    prod = dict(env="production", store="dynamodb", cron_secret="c" * 32, auth_provider="cognito", cognito_region="us-east-1", cognito_user_pool_id="us-east-1_POOL",
                cognito_app_client_id="clientid1234")
    assert any("ENIGMA_DDB_TABLE" in p for p in Settings(**prod).validate_for_startup())
    assert not Settings(**prod, ddb_table="enigma-dev").validate_for_startup()
    assert any("ENIGMA_STORE" in p for p in Settings(**{**prod, "store": "memory"}).validate_for_startup())


def test_create_app_builds_a_dynamodb_store_from_settings_and_serves_requests(ddb):
    s = Settings(env="test", dev_auth=True, dev_jwt_secret=DEV_SECRET, cron_secret="c" * 32, store="dynamodb", ddb_table=TABLE)
    app = create_app(s)                                            # no store injected: the factory must build DynamoStore itself (moto-backed client)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/api/v1/health").json()["store"] == "dynamodb"
        t = Tenant(c, "user_alice")
        assert t.workspace_id and c.get("/api/v1/app/workspaces", headers=t.h).json()["items"][0]["id"] == t.workspace_id
        assert isinstance(app.state.enigma.store, DynamoStore)


# ================================================================ strengthened parity checks (pagination, retries, conflicts, leases, TTL)
class PagingClient:
    """Forces DynamoDB-style pagination: every Query returns at most ``page`` items plus LastEvaluatedKey, whatever the caller asked for,
    and BatchGet/BatchWrite hand back half of the work as Unprocessed*. Exercises the store's continuation loops deterministically."""

    def __init__(self, inner, page=7):
        self._i, self.page, self.queries, self.unprocessed_seen = inner, page, 0, 0
        self._lock = threading.RLock()

    def __getattr__(self, name):
        fn = getattr(self._i, name)
        if name == "query":
            def q(**kw):
                self.queries += 1
                kw["Limit"] = min(kw.get("Limit", 10_000), self.page)
                return fn(**kw)
            return q
        if name == "batch_get_item":
            def bg(**kw):
                tbl = next(iter(kw["RequestItems"]))
                keys = kw["RequestItems"][tbl]["Keys"]
                if len(keys) > 1:
                    half = len(keys) // 2
                    first = fn(RequestItems={tbl: {**kw["RequestItems"][tbl], "Keys": keys[:half]}})
                    first["UnprocessedKeys"] = {tbl: {**kw["RequestItems"][tbl], "Keys": keys[half:]}}
                    self.unprocessed_seen += 1
                    return first
                return fn(**kw)
            return bg
        if name == "batch_write_item":
            def bw(**kw):
                tbl = next(iter(kw["RequestItems"]))
                reqs = kw["RequestItems"][tbl]
                if len(reqs) > 1:
                    half = len(reqs) // 2
                    fn(RequestItems={tbl: reqs[:half]})
                    self.unprocessed_seen += 1
                    return {"UnprocessedItems": {tbl: reqs[half:]}}
                return fn(**kw)
            return bw
        return fn


def _job(st, key="k-pg-12345"):
    ws = st.create_workspace("u1", "W", "w")["workspace"]
    pr = st.list_projects(ws["id"])[0]
    rt = st.create_runtime(ws["id"], pr["id"], "rt", True, "u1")
    cv = st.create_conversation(ws["id"], pr["id"], "u1", "c", rt["id"])
    st.submit_job(ws["id"], cv["id"], rt["id"], "u1", key, "go", {"max_tokens": 8, "temperature": 0.1}, 100000)
    return ws, pr, rt, cv, st.claim_job(rt["id"], "o", 120)


def test_events_pagination_returns_everything_in_order_and_honours_limit_and_cursor(ddb, clock):
    pc = PagingClient(ddb, page=7)
    st = DynamoStore(TABLE, client=pc, clock=clock)
    _ws, _pr, rt, _cv, job = _job(st)
    for start in range(1, 241, 60):                                                    # 240 events in four appends of 60 (<= 64 per request)
        assert st.append_events(rt["id"], job["id"], "o", [{"event_seq": i, "kind": "delta", "text": f"t{i};"} for i in range(start, start + 60)], 120)["ok"]
    allev = st.list_events(job["id"], 0, 1000)
    assert [e["event_seq"] for e in allev] == list(range(1, 241)) and pc.queries > 30      # many pages were followed
    assert [e["event_seq"] for e in st.list_events(job["id"], 100, 5)] == [101, 102, 103, 104, 105]      # cursor + limit across page boundaries
    assert st.list_events(job["id"], 240, 10) == []
    assert st.complete_job(rt["id"], job["id"], "o", 240, hashlib.sha256("".join(f"t{i};" for i in range(1, 241)).encode()).hexdigest(), {})["ok"]   # text rebuilt across pages


def test_message_and_telemetry_queries_paginate(ddb, clock):
    pc = PagingClient(ddb, page=5)
    st = DynamoStore(TABLE, client=pc, clock=clock)
    ws, pr, rt, cv, _ = _job(st)
    st.register_runtime(rt["id"], {"served_model": "m", "tp": 1, "pp": 1, "max_model_len": 2048, "gpus": [], "capabilities": {}, "sdk": {}})
    t0 = clock()
    rows = [{"captured_at": (t0 + timedelta(seconds=i)).isoformat(), "gpu_index": i % 2, "util_gpu_pct": float(i)} for i in range(61)]
    assert st.ingest_telemetry(rt["id"], "page-batch-0001", None, rows, [])["gpu_rows"] == 61
    got = st.query_gpu(pr["id"], None, t0 - timedelta(seconds=1), t0 + timedelta(minutes=5), 1000)
    assert len(got) == 61 and [g["util_gpu_pct"] for g in got] == [float(i) for i in range(61)] and pc.queries > 10
    assert len(st.query_gpu(pr["id"], rt["id"], t0, t0 + timedelta(minutes=5), 10)) == 10      # filtered + limited query still terminates correctly
    assert len(st.list_messages(cv["id"], limit=200)) == 1


def test_unprocessed_keys_and_items_are_retried_until_complete(ddb, clock):
    pc = PagingClient(ddb)
    st = DynamoStore(TABLE, client=pc, clock=clock)
    w = st.create_workspace("alice", "A", "alpha")["workspace"]["id"]
    for i in range(37):
        st.create_project(w, f"p{i}", "alice")
    assert len(st.list_projects(w)) == 38 and pc.unprocessed_seen > 0                  # BatchGetItem continuation
    ws, pr, rt, _cv, _ = _job(st)
    t0 = clock()
    rows = [{"captured_at": (t0 + timedelta(seconds=i)).isoformat(), "gpu_index": 0, "util_gpu_pct": 1.0} for i in range(60)]
    before = pc.unprocessed_seen
    assert st.ingest_telemetry(rt["id"], "unproc-batch-1", None, rows, [])["gpu_rows"] == 60
    assert pc.unprocessed_seen > before                                                # BatchWriteItem continuation
    assert len(st.query_gpu(pr["id"], None, t0, t0 + timedelta(minutes=2), 1000)) == 60       # nothing lost


def test_transaction_conflicts_and_conditional_failures_are_retried_without_duplicates(spy, clock):
    st = DynamoStore(TABLE, client=spy, clock=clock)
    ws = st.create_workspace("u1", "W", "w")["workspace"]
    pr = st.list_projects(ws["id"])[0]
    rt = st.create_runtime(ws["id"], pr["id"], "rt", True, "u1")
    cv = st.create_conversation(ws["id"], pr["id"], "u1", "c", rt["id"])
    # 1) a concurrent writer holds the items: TransactionConflict twice, then success
    spy.fail["transact_write_items"] = {"code": "TransactionCanceledException", "left": 2, "extra": {"CancellationReasons": [{"Code": "TransactionConflict"}] * 8}}
    r = st.submit_job(ws["id"], cv["id"], rt["id"], "u1", "k-conf-12345", "p", {"max_tokens": 8, "temperature": 0.1}, 1000)
    assert r["ok"] and len([m for m in st.list_messages(cv["id"]) if m["role"] == "user"]) == 1
    # 2) the runtime/user lock condition fails once (another submit won): the store re-reads and succeeds, still exactly one new job
    spy.fail.clear()
    spy.fail["transact_write_items"] = {"code": "TransactionCanceledException", "left": 1, "extra": {"CancellationReasons": [{"Code": "ConditionalCheckFailed"}] + [{"Code": "None"}] * 7}}
    r2 = st.submit_job(ws["id"], cv["id"], rt["id"], "u1", "k-conf-67890", "q", {"max_tokens": 8, "temperature": 0.1}, 1000)
    assert r2["ok"] and r2["job_id"] != r["job_id"]
    spy.fail.clear()
    assert len([m for m in st.list_messages(cv["id"]) if m["role"] == "user"]) == 2
    # 3) a permanent conflict ends in a clean error, never a half-written job
    spy.fail["transact_write_items"] = {"code": "TransactionCanceledException", "left": -1, "extra": {"CancellationReasons": [{"Code": "TransactionConflict"}] * 8}}
    with pytest.raises(StoreUnavailable) as e:                                         # a different user: u1 is already at its 2-active-job cap
        st.submit_job(ws["id"], cv["id"], rt["id"], "u2", "k-conf-99999", "r", {"max_tokens": 8, "temperature": 0.1}, 1000)
    assert "k-conf-99999" not in str(e.value)
    spy.fail.clear()
    assert [m["content"] for m in st.list_messages(cv["id"])] == ["p", "q"]                # nothing half-written


def test_throttling_during_claim_is_retried_and_the_job_is_claimed_once(spy, clock):
    st = DynamoStore(TABLE, client=spy, clock=clock)
    ws = st.create_workspace("u1", "W", "w")["workspace"]
    pr = st.list_projects(ws["id"])[0]
    rt = st.create_runtime(ws["id"], pr["id"], "rt", True, "u1")
    cv = st.create_conversation(ws["id"], pr["id"], "u1", "c", rt["id"])
    st.submit_job(ws["id"], cv["id"], rt["id"], "u1", "k-thr-12345", "p", {"max_tokens": 8, "temperature": 0.1}, 1000)
    spy.fail["transact_write_items"] = {"code": "ProvisionedThroughputExceededException", "left": 3}
    spy.fail["update_item"] = {"code": "ThrottlingException", "left": 2}
    j = st.claim_job(rt["id"], "owner", 30)
    assert j and j["attempt"] == 1
    spy.fail.clear()
    assert st.claim_job(rt["id"], "other", 30) is None


def test_runtime_active_job_cap_holds_across_different_users(spy, clock):
    st = DynamoStore(TABLE, client=spy, clock=clock)
    ws = st.create_workspace("owner", "W", "w")["workspace"]
    pr = st.list_projects(ws["id"])[0]
    rt = st.create_runtime(ws["id"], pr["id"], "rt", True, "owner")
    cv = st.create_conversation(ws["id"], pr["id"], "owner", "c", rt["id"])
    ok = 0
    for u in range(6):
        st.add_member(ws["id"], f"user{u}", "member")
        for k in range(2):                                                             # 2 active per user is allowed; the runtime cap (4) must stop us first
            r = st.submit_job(ws["id"], cv["id"], rt["id"], f"user{u}", f"key-{u}-{k}-abcdef", "p", {"max_tokens": 8, "temperature": 0.1}, 1000)
            ok += 1 if r.get("ok") else 0
            if not r.get("ok"):
                assert r["error"] == "too_many_jobs"
    assert ok == 4


def test_expiry_and_cancel_transitions_with_the_injected_clock(store, clock):
    ws = store.create_workspace("u1", "W", "w")["workspace"]
    pr = store.list_projects(ws["id"])[0]
    rt = store.create_runtime(ws["id"], pr["id"], "rt", True, "u1")
    cv = store.create_conversation(ws["id"], pr["id"], "u1", "c", rt["id"])
    a = store.submit_job(ws["id"], cv["id"], rt["id"], "u1", "k-exp-aaaaaa", "p", {"max_tokens": 8, "temperature": 0.1}, 1000)["job_id"]
    clock.advance(601)                                                                  # pending past its 10-minute deadline
    assert store.get_job(a)["status"] == "expired" and store.claim_job(rt["id"], "o", 30) is None
    b = store.submit_job(ws["id"], cv["id"], rt["id"], "u1", "k-exp-bbbbbb", "p", {"max_tokens": 8, "temperature": 0.1}, 1000)["job_id"]
    store.claim_job(rt["id"], "o", 30)
    assert store.request_cancel(ws["id"], b)["status"] == "cancel_requested"
    clock.advance(31)                                                                   # nobody acks and the lease is gone
    assert store.get_job(b)["status"] == "cancelled"
    c = store.submit_job(ws["id"], cv["id"], rt["id"], "u1", "k-exp-cccccc", "p", {"max_tokens": 8, "temperature": 0.1}, 1000)["job_id"]
    assert store.claim_job(rt["id"], "o1", 30)["attempt"] == 1
    clock.advance(31)
    assert store.claim_job(rt["id"], "o2", 30)["attempt"] == 2                          # lease expired, one attempt left: re-claimed
    clock.advance(31)
    assert store.claim_job(rt["id"], "o3", 30) is None and store.get_job(c)["status"] == "failed" and store.get_job(c)["error_code"] == "lease_expired"


def test_ttl_is_lazy_so_correctness_never_depends_on_it(store, clock, client):
    a = Tenant(client, "user_alice")
    a.create_runtime(); a.register()
    conv = a.conversation()
    job = a.submit(conv, "p").json()["job_id"]
    a.claim(); a.events(job, [{"event_seq": 1, "kind": "delta", "text": "x"}])
    client.post(f"/api/v1/jobs/{job}/complete", headers=a.rh, json={"final_seq": 1, "text_sha256": sha("x")})
    clock.advance(30 * 86400)                                                           # far beyond every ttl: real DynamoDB may not have deleted anything yet (up to ~48 h lag)
    assert [e["event_seq"] for e in store.list_events(job, 0, 10)] == [1]               # still readable and consistent: reads never filter on ttl
    assert store.get_job(job)["status"] == "succeeded"
    assert store.prune()["events"] == 1 and store.list_events(job, 0, 10) == []         # exact deletion happens only when prune runs (or when AWS TTL gets to it)


def test_conflicting_idempotency_key_across_conversations_is_rejected(store):
    ws = store.create_workspace("u1", "W", "w")["workspace"]
    pr = store.list_projects(ws["id"])[0]
    rt = store.create_runtime(ws["id"], pr["id"], "rt", True, "u1")
    c1 = store.create_conversation(ws["id"], pr["id"], "u1", "c1", rt["id"])
    c2 = store.create_conversation(ws["id"], pr["id"], "u1", "c2", rt["id"])
    assert store.submit_job(ws["id"], c1["id"], rt["id"], "u1", "same-key-abcdef", "p", {"max_tokens": 8, "temperature": 0.1}, 1000)["ok"]
    assert store.submit_job(ws["id"], c2["id"], rt["id"], "u1", "same-key-abcdef", "p", {"max_tokens": 8, "temperature": 0.1}, 1000) == {"error": "idempotency_conflict"}


def test_batch_read_never_returns_a_silently_truncated_result(ddb, clock):
    """If DynamoDB keeps returning UnprocessedKeys, the store must fail loudly (503-class), not hand back a partial list."""
    class NeverFinishes:
        def __init__(self, inner): self._i = inner
        def __getattr__(self, n):
            fn = getattr(self._i, n)
            if n != "batch_get_item":
                return fn
            def bg(**kw):
                tbl = next(iter(kw["RequestItems"]))
                return {"Responses": {tbl: []}, "UnprocessedKeys": {tbl: kw["RequestItems"][tbl]}}      # processes nothing, ever
            return bg
    st = DynamoStore(TABLE, client=ddb, clock=clock)
    w = st.create_workspace("alice", "A", "alpha")["workspace"]["id"]
    st.create_project(w, "p1", "alice")
    stuck = DynamoStore(TABLE, client=NeverFinishes(ddb), clock=clock)
    with pytest.raises(StoreUnavailable, match="incomplete"):
        stuck.list_projects(w)


def test_ready_probe_needs_only_getitem_and_works_on_an_empty_table(store):
    store.ready()                                              # absent key read: must not raise and must not create anything
