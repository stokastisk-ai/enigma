#!/usr/bin/env python3.13
"""Owner-run, verified identity linking: give a Neon Better Auth account access to the workspaces of an existing Cognito account.

Why a script and not an endpoint: linking GRANTS access to someone's workspaces. A network-exposed linking flow is an extra privilege-granting
attack surface, so the only supported way today is an operator who has verified, out of band, that both accounts belong to the same person
(e.g. they signed in to each and compared the `user_id` shown by GET /api/v1/app/me). Identities are NEVER merged by e-mail address.

    ENIGMA_LINK_DSN='postgresql://...'  python3.13 scripts/link_identity.py --from cognito:<sub> --to neon:<uuid>            # dry run (default)
    ENIGMA_LINK_DSN=...                 python3.13 scripts/link_identity.py --from cognito:<sub> --to neon:<uuid> --apply
    ENIGMA_LINK_DSN=...                 python3.13 scripts/link_identity.py --from cognito:<sub> --to neon:<uuid> --unlink   # exact reversal (dry run unless --apply)

* The Cognito memberships are KEPT (rollback path); the Neon account gets the same role in each workspace (ADDITIVE: existing Neon roles are never changed).
* Runtimes, API keys and projects belong to workspaces, so they are shared through the membership; no key is rotated or revoked.
* Needs a role allowed to write `memberships`/`audit_events` and read `neon_auth."user"` (the Neon owner role). Never pass the DSN on a command line.
* Each applied change writes one audit_events row (actor = 'operator:link_identity'); no e-mail address or secret is stored in it.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import psycopg
from psycopg.types.json import Jsonb

FROM_RE = re.compile(r"^cognito:[A-Za-z0-9_-]{1,128}$")
TO_RE = re.compile(r"^neon:[A-Za-z0-9_-]{1,128}$")


def mask(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:2]}***@{domain}" if domain else "***"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", required=True, help="existing identity, e.g. cognito:<sub>")
    ap.add_argument("--to", dest="dst", required=True, help="Neon identity, e.g. neon:<uuid> (the user_id GET /api/v1/app/me returns)")
    ap.add_argument("--apply", action="store_true", help="write the change (default: dry run)")
    ap.add_argument("--unlink", action="store_true", help="remove the memberships a previous link created for --to in --from's workspaces")
    a = ap.parse_args(argv)
    if not FROM_RE.fullmatch(a.src) or not TO_RE.fullmatch(a.dst):
        print("refused: --from must look like cognito:<sub> and --to like neon:<id> (identities are never matched by e-mail)", file=sys.stderr)
        return 2
    dsn = os.environ.get("ENIGMA_LINK_DSN", "")
    if not dsn:
        print("refused: set ENIGMA_LINK_DSN in the environment (never on the command line)", file=sys.stderr)
        return 2
    neon_id = a.dst.split(":", 1)[1]
    try:
        with psycopg.connect(dsn, autocommit=False, connect_timeout=15) as c:
            row = c.execute('select email, "emailVerified", banned from neon_auth."user" where id = %s', (neon_id,)).fetchone()
            if not row:
                print("refused: that Neon user does not exist", file=sys.stderr)
                return 3
            email, verified, banned = row
            if not verified or banned:
                print("refused: the Neon account must have a verified e-mail and not be banned", file=sys.stderr)
                return 3
            src = c.execute("select workspace_id, role from memberships where user_id = %s order by created_at", (a.src,)).fetchall()
            if not src:
                print("refused: the source identity owns/belongs to no workspace (check the id)", file=sys.stderr)
                return 3
            print(f"{'UNLINK' if a.unlink else 'LINK'} {a.src}  ->  {a.dst}   (Neon account e-mail, masked: {mask(email)})")
            changed = 0
            for ws, role in src:
                have = c.execute("select role from memberships where workspace_id = %s and user_id = %s", (ws, a.dst)).fetchone()
                if a.unlink:
                    if have and have[0] == role:
                        print(f"  workspace {ws}: remove {role} membership")
                        changed += 1
                        if a.apply:
                            c.execute("delete from memberships where workspace_id = %s and user_id = %s", (ws, a.dst))
                            c.execute("insert into audit_events (workspace_id, actor, action, target_type, target_id, meta) values (%s,'operator:link_identity','identity.unlink','user',%s,%s)",
                                      (ws, a.dst, Jsonb({"from_provider": "cognito", "role": role})))
                    else:
                        print(f"  workspace {ws}: nothing to remove")
                elif have:
                    print(f"  workspace {ws}: already a member as {have[0]} (left unchanged)")
                else:
                    print(f"  workspace {ws}: add as {role}")
                    changed += 1
                    if a.apply:
                        c.execute("insert into memberships (workspace_id, user_id, role) values (%s,%s,%s) on conflict do nothing", (ws, a.dst, role))
                        c.execute("insert into audit_events (workspace_id, actor, action, target_type, target_id, meta) values (%s,'operator:link_identity','identity.link','user',%s,%s)",
                                  (ws, a.dst, Jsonb({"from_provider": "cognito", "role": role})))
            if a.apply:
                c.commit()
                print(f"applied: {changed} change(s). The source identity's memberships were not touched.")
            else:
                c.rollback()
                print(f"dry run: {changed} change(s) would be made. Re-run with --apply to write them.")
    except psycopg.Error as e:
        print(f"database error: {getattr(e, 'sqlstate', None) or type(e).__name__}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
