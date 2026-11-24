"""PostgresStore conformance on a THROWAWAY local PostgreSQL cluster loaded with infra/aws/sql.

* Re-runs every store-agnostic test of the existing API suite (jobs, tenancy, route guards) against the real SQL store, with the API
  connecting as a non-superuser LOGIN role that only inherits ``enigma_app`` (the same privilege shape planned for RDS).
* Tests that depend on the in-memory fake clock are not imported (SQL uses server ``now()``); lease expiry is instead exercised here by
  rewinding ``lease_expires_at`` as the admin role.

This is LOCAL PostgreSQL evidence only. It says nothing about RDS reachability, TLS, IAM or latency (BLOCKED until an approved instance exists).
"""
from __future__ import annotations

import inspect
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sql"))
import run_sql_tests  # noqa: E402  (find_pg_bin, ROOT)

from api_helpers import DEV_SECRET, Clock, Tenant, sha  # noqa: E402
from enigma_api.main import create_app  # noqa: E402
from enigma_api.realtime import RecordingBroadcaster  # noqa: E402
from enigma_api.settings import Settings  # noqa: E402
from enigma_api.store.postgres import PostgresStore  # noqa: E402
from enigma_api.store.supabase import StoreUnavailable  # noqa: E402

PGBIN = run_sql_tests.find_pg_bin()
pytestmark = pytest.mark.skipif(PGBIN is None, reason="PostgreSQL server binaries not installed")
SQL_DIR = run_sql_tests.ROOT / "infra" / "aws" / "sql"
TABLES = ("audit_events,ingest_batches,vllm_metric_samples,gpu_samples,inference_events,inference_jobs,messages,conversations,"
          "inference_runs,runtime_credentials,runtimes,models,projects,memberships,workspaces")


@pytest.fixture(scope="module")
def pg():
    tmp = Path(tempfile.mkdtemp(prefix="enigma-pgapi-"))
    sock = Path(tempfile.mkdtemp(prefix="pgs", dir="/tmp"))
    port = "54331"
    env = {**os.environ, "LC_ALL": "C.UTF-8"}
    try:
        subprocess.run([PGBIN / "initdb", "-D", tmp / "d", "-U", "postgres", "--auth=trust", "-E", "UTF8"], check=True, capture_output=True, env=env)
        r = subprocess.run([PGBIN / "pg_ctl", "-D", tmp / "d", "-w", "-l", tmp / "pg.log", "-o", f"-c listen_addresses='' -k {sock} -p {port}", "start"],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        base = f"host={sock} port={port} "
        with psycopg.connect(base + "dbname=postgres user=postgres", autocommit=True) as c:
            c.execute("create database enigma")
        admin_dsn = base + "dbname=enigma user=postgres"
        with psycopg.connect(admin_dsn, autocommit=True) as c:
            for f in sorted(SQL_DIR.glob("*.sql")):
                c.execute(f.read_text())
            c.execute("create role enigma_api login nosuperuser nobypassrls in role enigma_app")
        yield {"app": base + "dbname=enigma user=enigma_api", "admin": admin_dsn}
    finally:
        subprocess.run([PGBIN / "pg_ctl", "-D", tmp / "d", "-m", "immediate", "stop"], capture_output=True, env=env)
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(sock, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean(pg):
    with psycopg.connect(pg["admin"], autocommit=True) as c:
        c.execute(f"truncate {TABLES} restart identity cascade")


@pytest.fixture
def bus():
    return RecordingBroadcaster()


@pytest.fixture
def clock():                                      # never advanced here: SQL uses the server clock
    return Clock()


@pytest.fixture
def store(pg):
    return PostgresStore(pg["app"])


@pytest.fixture
def client(store, bus, clock):
    s = Settings(env="test", dev_auth=True, dev_jwt_secret=DEV_SECRET, cron_secret="c" * 32, store="postgres", db_dsn="unused-store-injected")
    with TestClient(create_app(s, store=store, broadcaster=bus), raise_server_exceptions=False) as c:
        yield c


# ---- re-run the store-agnostic API tests against PostgreSQL
def _import_store_agnostic() -> None:
    import test_auth_and_tenancy, test_jobs, test_route_guards, test_telemetry    # noqa: E401
    skip_args = {"clock", "store", "monkeypatch", "kwargs", "expect"}
    for mod in (test_jobs, test_telemetry, test_auth_and_tenancy, test_route_guards):
        for name, fn in vars(mod).items():
            if name.startswith("test_") and inspect.isfunction(fn) and not (set(inspect.signature(fn).parameters) & skip_args):
                globals()[f"pg_{mod.__name__}_{name}"] = fn
    # pytest collects functions named test_*: rename to be collected
    for k in [k for k in globals() if k.startswith("pg_test_")]:
        globals()["test_" + k] = globals().pop(k)


_import_store_agnostic()


# ---- PostgreSQL-specific behaviour
def test_api_role_is_least_privilege(pg):
    with psycopg.connect(pg["app"], autocommit=True) as c:
        assert c.execute("select rolsuper or rolbypassrls or rolcreatedb or rolcreaterole from pg_roles where rolname = current_user").fetchone()[0] is False
        for stmt in ("create table nope(a int)", "truncate audit_events", "update audit_events set actor = 'x'", "delete from audit_events"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt)


def test_lease_expiry_reclaim_and_lockout_against_real_sql(client, pg, bus):
    a = Tenant(client, "user_alice")
    a.create_runtime()
    a.register()
    conv = a.conversation()
    job_id = a.submit(conv, "hi").json()["job_id"]
    assert a.claim().json()["job"]["attempt"] == 1
    assert a.events(job_id, [{"event_seq": 1, "kind": "delta", "text": "par"}]).status_code == 200
    with psycopg.connect(pg["admin"], autocommit=True) as c:          # the connector "died": rewind its lease
        c.execute("update inference_jobs set lease_expires_at = now() - interval '1 second' where id = %s", (job_id,))
    other = {**a.rh, "X-Enigma-Instance": "inst-2"}
    again = client.post("/api/v1/jobs/claim", json={}, headers=other).json()["job"]
    assert again["job_id"] == job_id and again["attempt"] == 2 and again["next_event_seq"] == 2     # sequence continues across attempts
    stale = a.events(job_id, [{"event_seq": 2, "kind": "delta", "text": "late"}])                    # old instance is locked out
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "lease_lost"
    ok = client.post(f"/api/v1/jobs/{job_id}/events", headers=other,
                     json={"events": [{"event_seq": 2, "kind": "reset", "text": ""}, {"event_seq": 3, "kind": "delta", "text": "fresh"}]})
    assert ok.json()["accepted"] == 2
    done = client.post(f"/api/v1/jobs/{job_id}/complete", headers=other, json={"final_seq": 3, "text_sha256": sha("fresh")})
    assert done.status_code == 200
    final = [m for m in client.get(f"/api/v1/app/conversations/{conv}/messages", headers=a.h).json()["items"] if m["role"] == "assistant"]
    assert [m["content"] for m in final] == ["fresh"]                                              # attempt 1's partial text was discarded


def test_concurrent_claims_hand_the_job_to_exactly_one_worker(pg, client):
    """SKIP LOCKED: N connections race for one pending job; exactly one wins."""
    import threading
    a = Tenant(client, "user_alice")
    a.create_runtime()
    a.register()
    a.submit(a.conversation(), "race")
    wins, errs = [], []

    def worker(i):
        try:
            st = PostgresStore(pg["app"])
            wins.append(st.claim_job(a.runtime_id, f"w{i}", 30))
        except Exception as e:                      # pragma: no cover
            errs.append(e)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and sum(1 for w in wins if w) == 1


def test_sql_injection_attempts_stay_data(client, pg):
    a = Tenant(client, "user_alice")
    a.create_runtime()
    a.register()
    conv = a.conversation()
    evil = "'); drop table messages; --"
    assert a.submit(conv, evil).status_code in (200, 201, 202)
    with psycopg.connect(pg["admin"]) as c:
        assert c.execute("select content from messages where role = 'user'").fetchone()[0] == evil
        assert c.execute("select to_regclass('public.messages')").fetchone()[0] is not None


def test_errors_never_echo_row_values_and_db_down_is_503(store):
    bad = PostgresStore("host=/nonexistent port=1 dbname=x user=y", connect_timeout_s=1)
    with pytest.raises(StoreUnavailable) as e:
        bad.get_role("secret-user-id", str(uuid.uuid4()))
    assert "secret-user-id" not in str(e.value)
    with pytest.raises(StoreUnavailable) as e:
        store.create_workspace("u", "n", "UPPER CASE BAD SLUG")        # violates the slug check; SQLSTATE only, no detail
    assert "UPPER CASE" not in str(e.value)


def test_dead_connection_recovers_for_the_next_request(store):
    assert store.get_role("nobody", str(uuid.uuid4())) is None
    store._conn.close()                                                # simulate a frozen-Lambda dead socket
    assert store.get_role("nobody", str(uuid.uuid4())) is None


def test_retention_prune_runs(store):
    assert "gpu_samples" in store.prune(0, 0, 0)


def test_ready_probe_succeeds_with_the_least_privilege_role_and_fails_closed_when_unreachable(store):
    store.ready()
    broken = PostgresStore("host=127.0.0.1 port=1 dbname=x user=x connect_timeout=1")
    with pytest.raises(StoreUnavailable) as e:
        broken.ready()
    assert "password" not in str(e.value).lower()


def test_verify_dsn_gets_a_ca_bundle_and_other_dsns_are_untouched():
    from enigma_api.store.postgres import _with_ca_bundle
    url = "postgresql://u:p@h.example/db?sslmode=verify-full&channel_binding=require"
    out = _with_ca_bundle(url)
    assert out.startswith(url + "&sslrootcert=") and out.endswith(".pem")
    assert _with_ca_bundle("host=h dbname=d sslmode=verify-ca").count("sslrootcert=") == 1
    assert _with_ca_bundle(url + "&sslrootcert=/x.pem") == url + "&sslrootcert=/x.pem"      # explicit setting wins
    assert _with_ca_bundle("postgresql://u:p@h/db?sslmode=require") == "postgresql://u:p@h/db?sslmode=require"


# ---------------------------------------------------------------- owner-run identity linking (scripts/link_identity.py)
def _load_link_script():
    import importlib.util
    spec = importlib.util.spec_from_file_location("link_identity", run_sql_tests.ROOT / "scripts" / "link_identity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def link(pg, monkeypatch, capsys):
    mod = _load_link_script()
    with psycopg.connect(pg["admin"], autocommit=True) as c:      # minimal stand-in for the table Neon Auth owns (real columns used by the script)
        c.execute('create schema if not exists neon_auth')
        c.execute('create table if not exists neon_auth."user" (id text primary key, email text, "emailVerified" boolean, banned boolean)')
        c.execute('truncate neon_auth."user"')
    monkeypatch.setenv("ENIGMA_LINK_DSN", pg["admin"])

    def run(*argv):
        rc = mod.main(list(argv))
        out = capsys.readouterr()
        return rc, out.out + out.err
    return run


def _seed(pg, roles=("owner", "viewer")):
    with psycopg.connect(pg["admin"], autocommit=True) as c:
        wss = []
        for i, role in enumerate(roles):
            ws = c.execute("insert into workspaces (name, slug, created_by) values (%s,%s,%s) returning id", (f"w{i}", f"w{i}", "cognito:old")).fetchone()[0]
            c.execute("insert into memberships (workspace_id, user_id, role) values (%s,'cognito:old',%s)", (ws, role))
            wss.append(ws)
        c.execute('insert into neon_auth."user" values (%s,%s,%s,%s)', ("n-good", "someone@example.test", True, False))
        return wss


def _members(pg):
    with psycopg.connect(pg["admin"]) as c:
        return sorted(c.execute("select user_id, role from memberships").fetchall())


def test_link_refuses_email_like_or_wrongly_namespaced_identities(pg, link):
    _seed(pg)
    for a, b in [("someone@example.test", "neon:n-good"), ("cognito:old", "someone@example.test"), ("neon:old", "neon:n-good"), ("cognito:old", "cognito:other")]:
        rc, _ = link("--from", a, "--to", b, "--apply")
        assert rc == 2
    assert _members(pg) == [("cognito:old", "owner"), ("cognito:old", "viewer")]


def test_link_dry_run_changes_nothing_then_apply_is_additive_audited_and_idempotent(pg, link):
    _seed(pg)
    before = _members(pg)
    rc, out = link("--from", "cognito:old", "--to", "neon:n-good")
    assert rc == 0 and "dry run: 2 change(s)" in out and "so***@example.test" in out and "someone@" not in out
    assert _members(pg) == before
    rc, out = link("--from", "cognito:old", "--to", "neon:n-good", "--apply")
    assert rc == 0 and "applied: 2" in out
    assert _members(pg) == sorted(before + [("neon:n-good", "owner"), ("neon:n-good", "viewer")])     # same roles; Cognito memberships untouched
    with psycopg.connect(pg["admin"]) as c:
        audit = c.execute("select actor, action, target_id, meta::text from audit_events order by id").fetchall()
    assert len(audit) == 2 and all(a[0] == "operator:link_identity" and a[1] == "identity.link" and a[2] == "neon:n-good" for a in audit)
    assert all("example.test" not in a[3] for a in audit)                                              # no e-mail in the audit trail
    rc, out = link("--from", "cognito:old", "--to", "neon:n-good", "--apply")
    assert rc == 0 and "applied: 0" in out and "already a member" in out


def test_link_never_changes_an_existing_neon_role(pg, link):
    wss = _seed(pg)
    with psycopg.connect(pg["admin"], autocommit=True) as c:
        c.execute("insert into memberships (workspace_id, user_id, role) values (%s,'neon:n-good','viewer')", (wss[0],))
    link("--from", "cognito:old", "--to", "neon:n-good", "--apply")
    with psycopg.connect(pg["admin"]) as c:
        assert c.execute("select role from memberships where workspace_id=%s and user_id='neon:n-good'", (wss[0],)).fetchone()[0] == "viewer"


def test_link_unlink_is_an_exact_reversal(pg, link):
    _seed(pg)
    before = _members(pg)
    link("--from", "cognito:old", "--to", "neon:n-good", "--apply")
    rc, out = link("--from", "cognito:old", "--to", "neon:n-good", "--unlink")
    assert rc == 0 and "dry run" in out and len(_members(pg)) == 4
    rc, out = link("--from", "cognito:old", "--to", "neon:n-good", "--unlink", "--apply")
    assert rc == 0 and _members(pg) == before


@pytest.mark.parametrize("row,why", [(("n-x", "u@example.test", False, False), "unverified"), (("n-x", "u@example.test", True, True), "banned")])
def test_link_refuses_unverified_or_banned_neon_accounts(pg, link, row, why):
    _seed(pg)
    with psycopg.connect(pg["admin"], autocommit=True) as c:
        c.execute('insert into neon_auth."user" values (%s,%s,%s,%s)', row)
    rc, _ = link("--from", "cognito:old", "--to", "neon:n-x", "--apply")
    assert rc == 3 and _members(pg) == [("cognito:old", "owner"), ("cognito:old", "viewer")]


def test_link_refuses_unknown_users_and_sources_without_workspaces(pg, link):
    _seed(pg)
    assert link("--from", "cognito:old", "--to", "neon:ghost", "--apply")[0] == 3
    assert link("--from", "cognito:nobody", "--to", "neon:n-good", "--apply")[0] == 3


def test_link_requires_the_dsn_in_the_environment(pg, link, monkeypatch):
    monkeypatch.delenv("ENIGMA_LINK_DSN")
    assert link("--from", "cognito:old", "--to", "neon:n-good")[0] == 2
