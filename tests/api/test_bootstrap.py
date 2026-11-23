"""enigma_api.bootstrap on a THROWAWAY local PostgreSQL cluster (unix socket only, removed afterwards). CPU-only; no network."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sql"))
import run_sql_tests  # noqa: E402

from enigma_api import bootstrap  # noqa: E402

PGBIN = run_sql_tests.find_pg_bin()
pytestmark = pytest.mark.skipif(PGBIN is None, reason="PostgreSQL server binaries not installed")
SQL_DIR = run_sql_tests.ROOT / "infra" / "aws" / "sql"
PASSWORD = "Zq7-bootstrap-test-password-0123456789"


@pytest.fixture
def pg():
    tmp = Path(tempfile.mkdtemp(prefix="enigma-pgboot-"))
    sock = Path(tempfile.mkdtemp(prefix="pgs", dir="/tmp"))
    port = "54333"
    env = {**os.environ, "LC_ALL": "C.UTF-8"}
    try:
        subprocess.run([PGBIN / "initdb", "-D", tmp / "d", "-U", "postgres", "--auth=trust", "-E", "UTF8"], check=True, capture_output=True, env=env)
        r = subprocess.run([PGBIN / "pg_ctl", "-D", tmp / "d", "-w", "-l", tmp / "pg.log", "-o", f"-c listen_addresses='' -k {sock} -p {port}", "start"],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        yield f"host={sock} port={port} dbname=postgres user=postgres"
    finally:
        subprocess.run([PGBIN / "pg_ctl", "-D", tmp / "d", "-m", "immediate", "stop"], capture_output=True, env=env)
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(sock, ignore_errors=True)


def env_for(dsn: str, **extra) -> dict:
    return {"ENIGMA_DB_ADMIN_DSN": dsn, "ENIGMA_DB_APP_PASSWORD": PASSWORD, "ENIGMA_SQL_DIR": str(SQL_DIR), **extra}


def test_bootstrap_creates_schema_and_a_least_privilege_role_and_is_idempotent(pg, capsys):
    assert bootstrap.run(env_for(pg)) == 0
    assert bootstrap.run(env_for(pg)) == 0                     # second run: schema skipped, functions re-applied, password rotated
    out = capsys.readouterr()
    text = out.out + out.err
    assert "skipping 0001" in text and "created" in text and "password rotated" in text
    assert PASSWORD not in text and "password=" not in text and pg not in text          # secrets never reach the logs
    with psycopg.connect(pg, autocommit=True) as c:
        assert c.execute("select to_regclass('public.workspaces') is not null").fetchone()[0]
        assert c.execute("select count(*) from pg_proc where proname like 'enigma_%'").fetchone()[0] > 5
        assert c.execute("select rolsuper or rolbypassrls or rolcreatedb or rolcreaterole from pg_roles where rolname='enigma_api'").fetchone()[0] is False
        assert c.execute("select pg_has_role('enigma_api','enigma_app','member')").fetchone()[0]
    with psycopg.connect(pg.replace("user=postgres", "user=enigma_api"), autocommit=True) as app:       # the real runtime role
        for stmt in ("create table nope(a int)", "truncate audit_events", "delete from audit_events"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(stmt)


def test_bootstrap_is_a_noop_without_an_admin_dsn(capsys):
    assert bootstrap.run({}) == 0
    assert "nothing to do" in capsys.readouterr().out


@pytest.mark.parametrize("extra,code", [({"ENIGMA_DB_APP_PASSWORD": "short"}, 2), ({"ENIGMA_DB_APP_ROLE": "postgres"}, 2), ({"ENIGMA_DB_APP_ROLE": "x; drop table y"}, 2),
                                         ({"ENIGMA_SQL_DIR": "/nonexistent"}, 2)])
def test_bootstrap_refuses_bad_configuration_before_touching_the_database(extra, code, capsys):
    env = env_for("host=127.0.0.1 port=1 dbname=x user=x connect_timeout=1")
    env.update(extra)
    assert bootstrap.run(env) == code
    assert "port=1" not in capsys.readouterr().out


def test_bootstrap_reports_only_a_sqlstate_class_when_the_database_is_unreachable(capsys):
    assert bootstrap.run(env_for("host=127.0.0.1 port=1 dbname=x user=x password=hunter2-secret connect_timeout=1")) == 3
    out = capsys.readouterr().out
    assert "database error" in out and "hunter2" not in out and "127.0.0.1" not in out
