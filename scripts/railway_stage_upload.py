#!/usr/bin/env python3
"""Build an EXACT, secret-scanned upload tree for one Railway service. Python 3.13, stdlib only. No network, no Railway/AWS/Git calls.

`railway up` uploads every file the CLI does not ignore, which here would include private untracked reports and local scripts. Instead
stage only what the service's Dockerfile needs into a directory OUTSIDE the repository and deploy that directory:

    python3.13 scripts/railway_stage_upload.py --service web --out /some/dir/outside/repo
    (after approval) railway up /some/dir/outside/repo --project <id> --service <name> --environment production --ci

Prints a manifest (path, bytes, sha256). Refuses: an --out inside the repo, any .env*/key/credential-looking file, any secret-pattern hit.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = {
    "web": ["apps/web", "packages/contracts"],
    "api": ["apps/api", "infra/aws/sql"],
}
DOCKERFILE = {"web": "apps/web/Dockerfile", "api": "apps/api/Dockerfile"}
EXCLUDE_DIRS = {"node_modules", "__pycache__", ".pytest_cache", ".git", ".enigma-local", "test-results", "playwright-report", "e2e", "e2e-live",
                ".amplify-hosting", "scripts"}
# Legacy AWS/Vercel entrypoints stay in the repo (fallback) but are never uploaded to Railway: the images do not use them.
EXCLUDE_FILES = {"apps/api/index.py", "apps/api/lambda_handler.py", "apps/api/vercel.json", "apps/api/requirements-aws.txt", "apps/api/requirements-dev.txt"}
EXCLUDE_GLOBS = [".next*", ".env*", "*.backup-*", "*.tsbuildinfo", "*.pyc", "*.zip", "*.whl", "*.log", "*.pem", "*.key", ".railway*", "playwright*.ts", "README.md"]
SECRET = re.compile(r"(AKIA[0-9A-Z]{16}|sk_(live|test)_[A-Za-z0-9]{10,}|pk_(live|test)_[A-Za-z0-9]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20,}|"
                    r"postgres(ql)?://[^\s:@/]+:[^\s@/]{4,}@|\b\d{12}\b.{0,10}arn:aws|arn:aws:[a-z0-9-]+:[a-z0-9-]*:\d{12}:)")


def excluded(name: str, is_dir: bool) -> bool:
    if is_dir and name in EXCLUDE_DIRS:
        return True
    return any(fnmatch.fnmatch(name, g) for g in EXCLUDE_GLOBS)


def collect(service: str) -> list[Path]:
    files: list[Path] = []
    for top in SERVICES[service]:
        base = ROOT / top
        if not base.is_dir():
            sys.exit(f"missing source directory: {top}")
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(ROOT)
            if any(excluded(part, True) if i < len(rel.parts) - 1 else excluded(part, p.is_dir()) for i, part in enumerate(rel.parts)):
                continue
            if p.is_file() and not p.is_symlink() and rel.as_posix() not in EXCLUDE_FILES:
                files.append(rel)
    if (ROOT / ".dockerignore").is_file():
        files.append(Path(".dockerignore"))
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--service", choices=sorted(SERVICES), required=True)
    ap.add_argument("--out", type=Path, required=True, help="empty or non-existent directory OUTSIDE the repository")
    a = ap.parse_args()
    out = a.out.expanduser().resolve()
    if out == ROOT or ROOT in out.parents or out in ROOT.parents:
        ap.error("--out must be outside the repository (the CLI would otherwise apply the repo's .gitignore and could upload nothing or everything)")
    if out.exists() and any(out.iterdir()):
        ap.error("--out must be empty or not exist (nothing is deleted by this tool)")
    files = collect(a.service)
    if Path(DOCKERFILE[a.service]) not in files:
        sys.exit(f"{DOCKERFILE[a.service]} is not in the staged set")
    problems = []
    for rel in files:
        if rel.name.startswith(".env") and rel.name != ".env.example":
            problems.append(f"env file: {rel}")
        txt = (ROOT / rel).read_bytes()
        if b"\0" not in txt[:4096] and SECRET.search(txt.decode("utf-8", "replace")):
            problems.append(f"secret-looking content: {rel}")
    if problems:
        print("REFUSING:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    total = 0
    print(f"# staged upload for service '{a.service}' -> {out}\n# sha256  bytes  path")
    for rel in files:
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
        b = dst.read_bytes()
        total += len(b)
        print(f"{hashlib.sha256(b).hexdigest()[:16]}  {len(b):>9}  {rel}")
    print(f"# {len(files)} files, {total/1024:.0f} KiB. Dockerfile: {DOCKERFILE[a.service]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
