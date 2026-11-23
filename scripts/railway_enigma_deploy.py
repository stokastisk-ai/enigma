#!/usr/bin/env python3
"""Railway Enigma deploy launcher: dry-run by default; explicit opt-in for a CLI upload of a STAGED directory.

Only the verified Enigma project (blissful-solace) and the named service/environment; NEVER touches AWS, Git or GPU.
It uploads a directory produced by scripts/railway_stage_upload.py (an exact allow-list, secret-scanned, outside the repository), never the
working tree: `railway up .` from the repo root would send every non-ignored untracked file (private reports, local scripts) to Railway.
Preconfigure the Dockerfile path (RAILWAY_DOCKERFILE_PATH), PORT and runtime variables in the dashboard first.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_EXPECTED = "github.com/stokastisk-ai/enigma"
# Verified read-only on 2026-11-24 (railway status). The launcher refuses any other project, so it cannot touch other Railway projects.
VERIFIED_PROJECT_ID = "6f38ca5c-3396-47e2-b897-825abb10b792"
VERIFIED_ENVIRONMENT = "production"
DOCKERFILE = {"enigma-web": "apps/web/Dockerfile", "Enigma": "apps/web/Dockerfile", "enigma-api": "apps/api/Dockerfile"}
CONFIRM = "DEPLOY-ENIGMA-RAILWAY"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-dir", type=Path, required=True, help="the Enigma repository (used only to verify the Git origin)")
    p.add_argument("--upload-dir", type=Path, required=True, help="staged directory from scripts/railway_stage_upload.py (outside the repository)")
    p.add_argument("--railway-project-id", required=True)
    p.add_argument("--service", required=True, choices=sorted(DOCKERFILE))
    p.add_argument("--environment", default=VERIFIED_ENVIRONMENT)
    p.add_argument("--execute", action="store_true", help="Actually upload and deploy using the Railway CLI")
    p.add_argument("--confirm", default="", help=f"Must equal {CONFIRM}")
    a = p.parse_args()
    root, up = a.project_dir.resolve(), a.upload_dir.resolve()
    if not (root / ".git").is_dir():
        p.error("Not a Git working tree")
    check = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True)
    if check.returncode or PROJECT_EXPECTED not in check.stdout:
        p.error("Unexpected Git origin, refusing deployment")
    if a.railway_project_id != VERIFIED_PROJECT_ID:
        p.error("Refusing: project id is not the verified Enigma project (blissful-solace)")
    if a.environment != VERIFIED_ENVIRONMENT:
        p.error("Refusing: only the verified 'production' environment of the Enigma project is allowed")
    if up == root or root in up.parents or up in root.parents:
        p.error("--upload-dir must be outside the repository so the CLI cannot apply or bypass repo ignore rules")
    if not (up / DOCKERFILE[a.service]).is_file():
        p.error(f"{DOCKERFILE[a.service]} not found in --upload-dir (stage it with scripts/railway_stage_upload.py)")
    if any(f.name.startswith(".env") and f.name != ".env.example" for f in up.rglob("*") if f.is_file()):
        p.error("Refusing: an .env* file is present in the upload directory")
    if not shutil.which("railway"):
        print("Railway CLI not on PATH. Install from official docs, then rerun.", file=sys.stderr)
        return 3
    command = ["railway", "up", str(up), "--service", a.service, "--environment", a.environment, "--project", a.railway_project_id, "--ci"]
    print("Project:", VERIFIED_PROJECT_ID, "| Railway service:", a.service, "| Environment:", a.environment)
    print("Upload dir:", up, "| Dockerfile:", DOCKERFILE[a.service])
    print("Command:", " ".join(command))
    print("This uploads a deployment and can incur Railway resource charges.")
    if not a.execute:
        print("DRY RUN. No cloud calls made.")
        return 0
    if a.confirm != CONFIRM:
        p.error(f"Execution requires --confirm {CONFIRM}")
    # Project tokens are preferable to workspace-wide API tokens; never print the environment.
    if os.environ.get("RAILWAY_API_TOKEN"):
        p.error("Refusing workspace/account-wide RAILWAY_API_TOKEN; use interactive login or a project-scoped RAILWAY_TOKEN")
    print("Launching approved Railway deployment; verify service config and billing limits before proceeding.")
    return subprocess.call(command)


if __name__ == "__main__":
    sys.exit(main())
