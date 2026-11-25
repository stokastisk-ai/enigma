"""Apply db/migrations (portable PostgreSQL) to a THROWAWAY local Postgres and run the job / isolation / privilege assertions.

stdlib only (shells out to initdb / pg_ctl / psql). The cluster lives in a fresh temp directory this script creates
and removes itself; it listens on a unix socket only, never a TCP port.

    python3.13 tests/sql/run_sql_tests.py

Requires PostgreSQL server binaries. Local evidence only; Neon is verified separately by the live checks.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = Path(__file__).resolve().parent


STEPS = [SQL_DIR / "pg_shim.sql", *sorted((ROOT / "db" / "migrations").glob("*.sql")), SQL_DIR / "test_jobs.sql"]


def find_pg_bin() -> Path | None:
    cands = sorted(glob.glob("/usr/lib/postgresql/*/bin/initdb"), reverse=True)
    if cands:
        return Path(cands[0]).parent
    w = shutil.which("initdb")
    return Path(w).parent if w else None


def run_all(steps: list[Path] | None = None) -> tuple[bool, str]:
    pgbin = find_pg_bin()
    if pgbin is None or not shutil.which("psql"):
        raise FileNotFoundError("PostgreSQL server binaries (initdb/pg_ctl) or psql not found")
    tmp = Path(tempfile.mkdtemp(prefix="enigma-pgtest-"))
    data = tmp / "data"
    # unix socket paths are limited to 107 bytes; TMPDIR can be longer, so only the (empty) socket dir goes to a short path
    sock = Path(tempfile.mkdtemp(prefix="pgs", dir=os.environ.get("ENIGMA_PG_SOCKET_DIR", "/tmp")))
    env = {**os.environ, "LC_ALL": "C.UTF-8", "PGHOST": str(sock), "PGPORT": "54329", "PGDATABASE": "postgres", "PGUSER": "postgres"}
    started = False
    try:
        subprocess.run([pgbin / "initdb", "-D", data, "-U", "postgres", "--auth=trust", "-E", "UTF8"], check=True, capture_output=True, env=env)
        r = subprocess.run([pgbin / "pg_ctl", "-D", data, "-w", "-l", tmp / "pg.log", "-o", f"-c listen_addresses='' -k {sock} -p 54329", "start"],
                           capture_output=True, text=True, env=env)
        if r.returncode != 0:
            log = (tmp / "pg.log").read_text(errors="replace")[-2000:] if (tmp / "pg.log").exists() else ""
            return False, f"postgres failed to start:\n{r.stdout}{r.stderr}\n{log}"
        started = True

        def psql(path: Path) -> subprocess.CompletedProcess:
            return subprocess.run(["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(path)], capture_output=True, text=True, env=env)

        steps = steps or STEPS
        out = ""
        for step in steps:
            r = psql(step)
            out += f"--- {step.name}\n{r.stdout}{r.stderr}"
            if r.returncode != 0:
                return False, out
        return True, out
    finally:
        if started:
            subprocess.run([pgbin / "pg_ctl", "-D", data, "-m", "immediate", "stop"], capture_output=True, env=env)
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(sock, ignore_errors=True)


if __name__ == "__main__":
    ok, log = run_all()
    print(log)
    sys.exit(0 if ok else 1)
