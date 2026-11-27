"""Operator CLI for Kaggle diagnostic keys (POST /api/v1/diag/events). Runs where the admin DSN is available (the ops container on the VM).

    python3 diag_keys.py --admin-dsn-file /import/admin.url --db enigma create --label nb03-oci-2026-11 --max-events 2000 --days 14
    python3 diag_keys.py --admin-dsn-file ... list
    python3 diag_keys.py --admin-dsn-file ... revoke --id abcd1234

`create` prints the key ONCE (store it as the Kaggle Secret ENIGMA_DIAG_KEY); only sha256(secret) is stored. `list` never prints secrets or hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import secrets
import string
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

import psycopg


def dsn_for(path: str, db: str) -> str:
    u = urllib.parse.urlsplit(open(path).read().strip())
    return urllib.parse.urlunsplit((u.scheme, u.netloc, "/" + db, u.query, u.fragment))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-dsn-file", required=True)
    ap.add_argument("--db", default="enigma")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--label", required=True)
    c.add_argument("--max-events", type=int, default=2000)
    c.add_argument("--days", type=int, default=14)
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("--id", required=True)
    a = ap.parse_args()
    with psycopg.connect(dsn_for(a.admin_dsn_file, a.db), autocommit=True, connect_timeout=10) as conn:
        if a.cmd == "create":
            if not 1 <= a.max_events <= 100000 or not 1 <= a.days <= 90:
                print("max-events 1..100000, days 1..90"); return 2
            kid = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
            secret = secrets.token_urlsafe(32)                       # 43 chars, [A-Za-z0-9_-]
            exp = datetime.now(timezone.utc) + timedelta(days=a.days)
            conn.execute("insert into diag_keys (id, secret_hash, label, max_events, expires_at) values (%s, %s, %s, %s, %s)",
                         (kid, hashlib.sha256(secret.encode()).hexdigest(), a.label[:80], a.max_events, exp))
            print(f"edx_{kid}_{secret}")
            print(f"(id {kid}, label {a.label[:80]}, max_events {a.max_events}, expires {exp:%Y-%m-%dT%H:%MZ}; shown once)", file=sys.stderr)
        elif a.cmd == "list":
            for row in conn.execute("select id, label, used, max_events, expires_at, revoked from diag_keys order by created_at"):
                print("{} {:<40} {:>6}/{:<6} expires {:%Y-%m-%d} {}".format(row[0], row[1], row[2], row[3], row[4], "REVOKED" if row[5] else ""))
        else:
            n = conn.execute("update diag_keys set revoked = true where id = %s", (a.id,)).rowcount
            print("revoked" if n else "no such key")
    return 0


if __name__ == "__main__":
    sys.exit(main())
