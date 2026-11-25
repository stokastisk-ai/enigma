"""One-shot, idempotent PostgreSQL bootstrap: ``python -m enigma_api.bootstrap``. Intended as a Railway PRE-DEPLOY command.

Applies the portable schema (db/migrations, copied to ``ENIGMA_SQL_DIR``) and (re)creates the least-privilege LOGIN role the API connects as.
Needs an ADMIN connection, so it only acts when ``ENIGMA_DB_ADMIN_DSN`` is set; remove that variable once bootstrapped and this becomes a no-op.

    ENIGMA_DB_ADMIN_DSN    libpq DSN of a role allowed to create tables/roles (secret; never printed or logged)
    ENIGMA_DB_APP_PASSWORD password for the application role (secret; never printed or logged)
    ENIGMA_DB_APP_ROLE     application role name (default: enigma_api)
    ENIGMA_SQL_DIR         directory holding 0001_core_schema.sql and 0002_job_functions.sql (default: ./sql next to the package)

The application role is NOSUPERUSER NOBYPASSRLS and only a member of the enigma_app group role, exactly as tests/api/test_postgres_store.py exercises.
Exit codes: 0 done or nothing to do; 2 misconfiguration; 3 database error (SQLSTATE only is reported: messages can carry values).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
SCHEMA_FILE, FUNCTIONS_FILE = "0001_core_schema.sql", "0002_job_functions.sql"


def say(msg: str) -> None:
    print(f"[enigma.bootstrap] {msg}", flush=True)


def run(env: dict[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    admin_dsn = env.get("ENIGMA_DB_ADMIN_DSN", "")
    if not admin_dsn:
        say("ENIGMA_DB_ADMIN_DSN not set: nothing to do")
        return 0
    role = env.get("ENIGMA_DB_APP_ROLE", "enigma_api")
    password = env.get("ENIGMA_DB_APP_PASSWORD", "")
    sql_dir = Path(env.get("ENIGMA_SQL_DIR") or Path(__file__).resolve().parent.parent / "sql")
    if not ROLE_RE.fullmatch(role) or role in ("postgres", "enigma_app"):
        say("ENIGMA_DB_APP_ROLE is invalid")
        return 2
    if len(password) < 24:
        say("ENIGMA_DB_APP_PASSWORD must be set and at least 24 characters")
        return 2
    files = {n: sql_dir / n for n in (SCHEMA_FILE, FUNCTIONS_FILE)}
    if not all(p.is_file() for p in files.values()):
        say(f"schema files not found in {sql_dir}")
        return 2
    try:
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=10) as c:
            if c.execute("select to_regclass('public.workspaces') is not null").fetchone()[0]:
                say("core schema already present: skipping 0001")
            else:
                c.execute(files[SCHEMA_FILE].read_text())
                say("applied 0001_core_schema.sql")
            c.execute(files[FUNCTIONS_FILE].read_text())              # create or replace: safe to repeat
            say("applied 0002_job_functions.sql")
            for extra in sorted(p for p in sql_dir.glob("*.sql") if p.name not in (SCHEMA_FILE, FUNCTIONS_FILE)):
                c.execute(extra.read_text())                          # later migrations are written to be idempotent
                say(f"applied {extra.name}")
            exists = c.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
            # Neon's owner is not a superuser and may not even *state* NOSUPERUSER in ALTER ROLE (42501); the role is created NOSUPERUSER and the
            # verification below proves it still is, so the clause is only used on create.
            attrs = "login nobypassrls nocreatedb nocreaterole" if exists else "login nosuperuser nobypassrls nocreatedb nocreaterole"
            c.execute(sql.SQL(("alter" if exists else "create") + " role {} " + attrs + " password {}").format(sql.Identifier(role), sql.Literal(password)))
            if not exists:
                c.execute(sql.SQL("grant enigma_app to {}").format(sql.Identifier(role)))
            say(f"role {role}: {'password rotated' if exists else 'created'}")
            row = c.execute("select rolsuper or rolbypassrls or rolcreatedb or rolcreaterole from pg_roles where rolname = %s", (role,)).fetchone()
            member = c.execute("select pg_has_role(%s, 'enigma_app', 'member')", (role,)).fetchone()[0]
            if row[0] or not member:
                say("verification FAILED: role is over-privileged or not a member of enigma_app")
                return 3
            say("verified: least-privilege role, member of enigma_app")
    except psycopg.Error as e:
        say(f"database error: {getattr(e, 'sqlstate', None) or type(e).__name__}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(run())
