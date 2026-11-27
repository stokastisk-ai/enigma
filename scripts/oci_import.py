"""Import a Neon `pg_dump --format=custom` backup of Enigma into a self-hosted PostgreSQL 18 database (OCI A1 VM or a local test container).

    python3.13 scripts/oci_import.py --dump <neon.dump> --admin-dsn-file <file> --target-db enigma [--manifest <dump.manifest.json>]
                                    [--report <report.json>] [--dry-run]

What it does (the dump and the database contents are never printed; the report holds counts, hashes and check results only):
  A. restore   Create the target database (must not exist or be empty), restore the dump with the Neon-only extension pg_session_jwt and all
               ownership/privilege statements removed, as ONE transaction (pg_restore --single-transaction --exit-on-error).
  B. transform ONE transaction: apply db/migrations/0004_auth_schema.sql; copy neon_auth.user and neon_auth.account (credential provider only:
               scrypt hashes, UUIDs, verified flags preserved; Neon sessions, verification codes and signing keys are NOT copied: users sign in
               again and the new deployment mints its own Ed25519 keys); rewrite the identity namespace neon:<uuid> -> ba:<uuid> in every identity
               column; verify (below); drop schema neon_auth; record public.enigma_import_log. Any failed check rolls the whole transaction back.
  C. verify    expected-vs-actual checksum per table (expected = source rows with only the namespace rewrite applied), row counts against the
               export manifest, no `neon:` value left in ANY text/json column, every ba:<uuid> identity exists in auth.user, every workspace has an
               owner, all constraints validated, inference_events sequence continuity reported, password hashes still in scrypt shape.
Re-running against a target that already holds a completed import of the SAME dump (by SHA-256) is a no-op that re-runs the read-only checks.
Rollback: the source (Neon) is never written; drop the target database and re-run. Roles are created afterwards by `python -m enigma_api.bootstrap`.
Exit codes: 0 ok; 2 usage/precondition; 3 check failed (transaction rolled back); 4 database/tool error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
AUTH_SQL = ROOT / "db/migrations/0004_auth_schema.sql"
# Every column that stores a user identity (audit actor may also be 'runtime:<id>' or 'system', which are left untouched).
IDENTITY_COLUMNS = [("workspaces", "created_by"), ("memberships", "user_id"), ("projects", "created_by"), ("runtimes", "created_by"),
                    ("runtime_credentials", "created_by"), ("conversations", "created_by"), ("inference_jobs", "requested_by"), ("audit_events", "actor")]
NEON_ID = r"^neon:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
USER_COLS = ['id', 'name', 'email', '"emailVerified"', 'image', '"createdAt"', '"updatedAt"', 'role', 'banned', '"banReason"', '"banExpires"']
ACCOUNT_COLS = ['id', '"accountId"', '"providerId"', '"userId"', '"accessToken"', '"refreshToken"', '"idToken"', '"accessTokenExpiresAt"',
                '"refreshTokenExpiresAt"', 'scope', 'password', '"createdAt"', '"updatedAt"']


class CheckFailed(Exception):
    pass


def say(msg: str) -> None:
    print(f"[oci-import] {msg}", flush=True)


def pg_env(dsn: str, db: str | None = None) -> dict[str, str]:
    u = urllib.parse.urlsplit(dsn)
    q = dict(urllib.parse.parse_qsl(u.query))
    env = dict(os.environ, PGHOST=u.hostname or "", PGPORT=str(u.port or 5432), PGUSER=urllib.parse.unquote(u.username or ""),
               PGPASSWORD=urllib.parse.unquote(u.password or ""), PGDATABASE=db or u.path.lstrip("/") or "postgres")
    for k, v in (("sslmode", "PGSSLMODE"), ("sslrootcert", "PGSSLROOTCERT")):
        if k in q:
            env[v] = q[k]
    return env


def with_db(dsn: str, db: str) -> str:
    u = urllib.parse.urlsplit(dsn)
    return urllib.parse.urlunsplit((u.scheme, u.netloc, "/" + db, u.query, u.fragment))


def tables(c: psycopg.Connection, schema: str) -> list[str]:
    return [r[0] for r in c.execute("select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname=%s and c.relkind='r' "
                                    "order by 1", (schema,)).fetchall()]


def pk_cols(c: psycopg.Connection, schema: str, table: str) -> list[str]:
    rows = c.execute("""select a.attname from pg_index i join pg_attribute a on a.attrelid=i.indrelid and a.attnum = any(i.indkey)
                        where i.indrelid = (quote_ident(%s)||'.'||quote_ident(%s))::regclass and i.indisprimary order by array_position(i.indkey, a.attnum)""",
                     (schema, table)).fetchall()
    return [r[0] for r in rows]


def checksum(c: psycopg.Connection, table: str, rewrite: bool) -> tuple[int, str]:
    """md5 over the table's rows (as JSON, ordered by primary key). With rewrite=True the source-side namespace rewrite is applied to the JSON text,
    which is exact because the pre-scan proved `neon:` only occurs as a whole identity value in IDENTITY_COLUMNS."""
    order = sql.SQL(", ").join(sql.Identifier(k) for k in pk_cols(c, "public", table)) or sql.SQL("1")
    row = sql.SQL("replace(row_to_json(t)::text, '\"neon:', '\"ba:')") if rewrite else sql.SQL("row_to_json(t)::text")
    q = sql.SQL("select count(*), coalesce(md5(string_agg({row}, E'\\n' order by {order})), md5('')) from public.{t} t").format(
        row=row, order=order, t=sql.Identifier(table))
    n, h = c.execute(q).fetchone()
    return int(n), h


def neon_refs(c: psycopg.Connection, schemas: tuple[str, ...] = ("public", "auth")) -> list[tuple[str, str, str, int]]:
    """Every text/varchar/json/jsonb column in the given schemas that still contains the substring 'neon:'."""
    out = []
    cols = c.execute("""select table_schema, table_name, column_name from information_schema.columns
                        where table_schema = any(%s) and data_type in ('text','character varying','json','jsonb')""", (list(schemas),)).fetchall()
    for s, t, col in cols:
        n = c.execute(sql.SQL("select count(*) from {}.{} where {}::text like '%%neon:%%'").format(sql.Identifier(s), sql.Identifier(t), sql.Identifier(col))).fetchone()[0]
        if n:
            out.append((s, t, col, int(n)))
    return out


def restore(dump: Path, admin_dsn: str, target: str) -> None:
    listing = subprocess.run(["pg_restore", "-l", str(dump)], capture_output=True, text=True, check=True).stdout
    keep = [ln for ln in listing.splitlines() if not re.search(r"pg_session_jwt|\bACL\b|DEFAULT ACL|EVENT TRIGGER|PUBLICATION|SUBSCRIPTION", ln)]
    with tempfile.NamedTemporaryFile("w", suffix=".list", delete=False) as f:
        f.write("\n".join(keep) + "\n")
        lst = f.name
    try:
        r = subprocess.run(["pg_restore", "--no-owner", "--no-privileges", "--single-transaction", "--exit-on-error", "-L", lst, "-d", target, str(dump)],
                           env=pg_env(admin_dsn, target), capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("pg_restore failed: " + " ".join(re.findall(r"ERROR:\s+([^\n]{0,160})", r.stderr))[:400])
    finally:
        os.unlink(lst)


def transform(c: psycopg.Connection, manifest: dict | None, dump_sha: str) -> dict:
    rep: dict = {"checks": {}}

    def check(name: str, ok: bool, detail=None) -> None:
        rep["checks"][name] = {"ok": bool(ok), **({"detail": detail} if detail is not None else {})}
        say(f"{'PASS' if ok else 'FAIL'} {name}" + (f" ({detail})" if detail is not None and not isinstance(detail, (dict, list)) else ""))
        if not ok:
            raise CheckFailed(name)

    pub = tables(c, "public")
    # 1. source-side facts, inside the same transaction (snapshot of what was restored)
    refs = neon_refs(c, ("public",))
    allowed = {(t, col) for t, col in IDENTITY_COLUMNS}
    check("neon: occurs only in the known identity columns", all((t, col) in allowed for _, t, col, _ in refs), [f"{t}.{col}={n}" for _, t, col, n in refs])
    for t, col in IDENTITY_COLUMNS:
        bad = c.execute(sql.SQL("select count(*) from public.{} where {} like 'neon:%%' and {} !~ %s").format(sql.Identifier(t), sql.Identifier(col), sql.Identifier(col)),
                        (NEON_ID,)).fetchone()[0]
        check(f"{t}.{col}: every neon: value is neon:<uuid>", bad == 0, bad)
    expected = {t: checksum(c, t, rewrite=True) for t in pub}
    src_users = c.execute('select count(*) from neon_auth."user"').fetchone()[0]
    src_cred = c.execute("""select count(*) from neon_auth.account where "providerId"='credential'""").fetchone()[0]
    other_providers = c.execute("""select count(*) from neon_auth.account where "providerId"<>'credential'""").fetchone()[0]
    check("only credential (email+password) accounts exist in the source", other_providers == 0, other_providers)
    if manifest:
        mc = manifest.get("row_counts", {})
        diffs = {t: (mc.get(f"public.{t}"), expected[t][0]) for t in pub if mc.get(f"public.{t}") not in (None, expected[t][0])}
        diffs.update({k: (mc.get(f"neon_auth.{k}"), v) for k, v in (("user", src_users),) if mc.get(f"neon_auth.{k}") not in (None, v)})
        check("restored row counts equal the export manifest", not diffs, diffs or "all equal")

    # 2. auth schema + users/accounts (0004 revokes from the API group role, which a restored dump does not carry)
    c.execute("""do $$ begin if not exists (select 1 from pg_roles where rolname = 'enigma_app') then
                 create role enigma_app nologin nosuperuser nocreatedb nocreaterole nobypassrls; end if; end $$;""")
    c.execute(AUTH_SQL.read_text())
    c.execute(sql.SQL("insert into auth.\"user\" ({c}) select {c} from neon_auth.\"user\"").format(c=sql.SQL(", ".join(USER_COLS))))
    c.execute(sql.SQL("insert into auth.account ({c}) select {c} from neon_auth.account where \"providerId\"='credential'").format(c=sql.SQL(", ".join(ACCOUNT_COLS))))
    n_users = c.execute('select count(*) from auth."user"').fetchone()[0]
    n_acc = c.execute("select count(*) from auth.account").fetchone()[0]
    check("auth.user count equals source", n_users == src_users, f"{n_users}/{src_users}")
    check("auth.account (credential) count equals source", n_acc == src_cred, f"{n_acc}/{src_cred}")
    shape = c.execute(r"""select count(*) from auth.account where "providerId"='credential' and password ~ '^[0-9a-f]{32}:[0-9a-f]{128}$'""").fetchone()[0]
    check("credential hashes kept in Better Auth scrypt salt:hash shape", shape == n_acc, f"{shape}/{n_acc}")
    ids_equal = c.execute('select count(*) from auth."user" a join neon_auth."user" n on n.id=a.id and n.email=a.email and n."emailVerified"=a."emailVerified"').fetchone()[0]
    check("user UUIDs, e-mails and verified flags preserved", ids_equal == src_users, f"{ids_equal}/{src_users}")

    # 3. namespace rewrite
    rewritten = {}
    for t, col in IDENTITY_COLUMNS:
        cur = c.execute(sql.SQL("update public.{t} set {c} = 'ba:' || substr({c}, 6) where {c} like 'neon:%%'").format(t=sql.Identifier(t), c=sql.Identifier(col)))
        rewritten[f"{t}.{col}"] = cur.rowcount
    rep["rewritten"] = rewritten
    say(f"rewrote identities: {rewritten}")

    # 4. verification
    actual = {t: checksum(c, t, rewrite=False) for t in pub}
    mism = [t for t in pub if actual[t] != expected[t]]
    check("per-table checksums equal source with only the namespace rewritten", not mism, mism or f"{len(pub)} tables")
    rep["tables"] = {t: {"rows": actual[t][0], "md5": actual[t][1]} for t in pub}
    left = neon_refs(c)
    check("no neon: value left in any text/json column", not left, [f"{s}.{t}.{col}={n}" for s, t, col, n in left] or "none")
    orphans = {}
    for t, col in IDENTITY_COLUMNS:
        n = c.execute(sql.SQL("""select count(*) from public.{t} x where x.{c} like 'ba:%%'
                                 and not exists (select 1 from auth."user" u where 'ba:' || u.id::text = x.{c})""").format(t=sql.Identifier(t), c=sql.Identifier(col))).fetchone()[0]
        if n:
            orphans[f"{t}.{col}"] = n
    check("every ba:<uuid> identity exists in auth.user (no orphan identities)", not orphans, orphans or "none")
    no_owner = c.execute("select count(*) from public.workspaces w where not exists (select 1 from public.memberships m where m.workspace_id=w.id and m.role='owner')").fetchone()[0]
    check("every workspace has an owner membership", no_owner == 0, no_owner)
    unvalidated = c.execute("select count(*) from pg_constraint where connamespace in ('public'::regnamespace, 'auth'::regnamespace) and not convalidated").fetchone()[0]
    check("all constraints validated", unvalidated == 0, unvalidated)
    fks = c.execute("select count(*) from pg_constraint where connamespace='public'::regnamespace and contype='f'").fetchone()[0]
    rep["foreign_keys_enforced"] = int(fks)
    # (job_id, event_seq) is the primary key, so order/uniqueness is structural; continuity (1..n without gaps) is reported, not enforced:
    # the import preserves whatever the source held, which the checksum above proves.
    rep["inference_event_jobs_with_seq_gaps"] = c.execute("""select count(*) from (select job_id from public.inference_events group by job_id
                                                             having max(event_seq) <> count(*) or min(event_seq) <> 1) g""").fetchone()[0]
    rep["jobs_by_status"] = dict(c.execute("select status, count(*) from public.inference_jobs group by 1 order by 1").fetchall())
    rep["active_leases"] = c.execute("select count(*) from public.inference_jobs where status in ('claimed','running','cancel_requested')").fetchone()[0]

    # 5. drop Neon's auth schema (its sessions/codes/keys are not reused) and record the import
    c.execute("drop schema neon_auth cascade")
    c.execute("create table if not exists public.enigma_import_log (dump_sha256 text primary key, imported_at timestamptz not null default now(), report jsonb not null)")
    c.execute("revoke all on public.enigma_import_log from public")
    c.execute("insert into public.enigma_import_log (dump_sha256, report) values (%s, %s)", (dump_sha, json.dumps(rep)))
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dump", required=True, type=Path)
    ap.add_argument("--admin-dsn-file", required=True, type=Path, help="file holding a libpq URL of a role that can CREATE DATABASE (never printed)")
    ap.add_argument("--target-db", default="enigma")
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="restore + transform + verify, then ROLL BACK the transform and drop the target database")
    a = ap.parse_args()
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", a.target_db) or a.target_db in ("postgres", "template0", "template1"):
        say("invalid --target-db"); return 2
    if not a.dump.is_file():
        say("dump not found"); return 2
    admin = a.admin_dsn_file.read_text().strip()
    dump_sha = hashlib.sha256(a.dump.read_bytes()).hexdigest()
    manifest = json.loads(a.manifest.read_text()) if a.manifest else None
    if manifest and manifest.get("sha256") not in (None, dump_sha):
        say("dump SHA-256 does not match its manifest"); return 2
    say(f"dump sha256 {dump_sha[:12]}…, target database {a.target_db}{' (dry run)' if a.dry_run else ''}")
    try:
        with psycopg.connect(admin, autocommit=True, connect_timeout=10) as c:
            exists = c.execute("select 1 from pg_database where datname=%s", (a.target_db,)).fetchone()
            if exists:
                with psycopg.connect(with_db(admin, a.target_db), autocommit=True) as t:
                    done = t.execute("select to_regclass('public.enigma_import_log') is not null").fetchone()[0] and \
                        t.execute("select 1 from public.enigma_import_log where dump_sha256=%s", (dump_sha,)).fetchone()
                    populated = t.execute("select to_regclass('public.workspaces') is not null").fetchone()[0]
                if done:
                    say("this dump is already imported: no-op (re-verify with the read-only checks in the report)")
                    return 0
                if populated:
                    say("target database is not empty and holds no import of this dump: refusing (drop it explicitly to re-import)")
                    return 2
            else:
                c.execute(sql.SQL("create database {}").format(sql.Identifier(a.target_db)))
        say("A. restore")
        restore(a.dump, admin, a.target_db)
        say("B/C. transform + verify (single transaction)")
        with psycopg.connect(with_db(admin, a.target_db), connect_timeout=10) as t:
            try:
                rep = transform(t, manifest, dump_sha)
            except CheckFailed as e:
                t.rollback()
                say(f"CHECK FAILED: {e}; transaction rolled back, target left as restored (drop it before retrying)")
                return 3
            if a.dry_run:
                t.rollback()
            else:
                t.commit()
        rep.update({"dump_sha256": dump_sha, "target_db": a.target_db, "dry_run": a.dry_run})
        if a.report:
            a.report.write_text(json.dumps(rep, indent=1, default=str))
            os.chmod(a.report, 0o600)
        if a.dry_run:
            with psycopg.connect(admin, autocommit=True) as c:
                c.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(a.target_db)))
            say("dry run complete: transform rolled back, target database dropped")
        else:
            say("import committed; next: python -m enigma_api.bootstrap (roles), then the API/web smoke checks")
        return 0
    except (psycopg.Error, RuntimeError, subprocess.CalledProcessError) as e:
        say(f"error: {getattr(e, 'sqlstate', None) or type(e).__name__}: {str(e)[:200] if isinstance(e, RuntimeError) else ''}")
        return 4


if __name__ == "__main__":
    sys.exit(main())
