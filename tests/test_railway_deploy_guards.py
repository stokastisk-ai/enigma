"""Guards on the Railway launcher and the staging tool. Only dry-run / refusal paths run: nothing here can call Railway, AWS or Git remotes."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts/railway_enigma_deploy.py"
STAGE = ROOT / "scripts/railway_stage_upload.py"
PROJECT_ID = "6f38ca5c-3396-47e2-b897-825abb10b792"


def run(script: Path, *args: str, env=None):
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, timeout=60, env=env)


@pytest.fixture
def fake_repo(tmp_path):
    """A throwaway local Git repo whose origin URL is the expected one (nothing is fetched): the launcher only reads `remote get-url origin`."""
    repo = tmp_path / "repo"
    (repo / "apps").mkdir(parents=True)
    for cmd in (["git", "init", "-q"], ["git", "remote", "add", "origin", "https://github.com/stokastisk-ai/enigma.git"]):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def staged_web(tmp_path):
    out = tmp_path / "stage_web"
    r = run(STAGE, "--service", "web", "--out", str(out))
    assert r.returncode == 0, r.stderr
    return out


def deploy(repo: Path, up: Path, *extra: str, project=PROJECT_ID, service="Enigma", env="production"):
    return run(DEPLOY, "--project-dir", str(repo), "--upload-dir", str(up), "--railway-project-id", project, "--service", service, "--environment", env, *extra)


def test_staged_web_tree_has_exactly_what_the_image_needs_and_nothing_private(staged_web):
    files = {p.relative_to(staged_web).as_posix() for p in staged_web.rglob("*") if p.is_file()}
    for need in ("apps/web/Dockerfile", "apps/web/Dockerfile.dockerignore", "apps/web/package.json", "apps/web/package-lock.json",
                 "packages/contracts/src/api.d.ts", ".dockerignore", "apps/web/proxy.ts"):
        assert need in files, need
    bad = [f for f in files if "node_modules" in f or ".env" in Path(f).name.replace(".env.example", "") or ".enigma-local" in f or f.endswith((".backup", ".zip"))
           or "/.next" in f or "/e2e" in f or ".aws" in f or f.startswith("scripts/") or f.startswith("docs/") or f == "skills.md" or f == "CLAUDE.md"]
    assert not bad, bad


def test_staged_api_tree_excludes_legacy_lambda_and_vercel_entrypoints(tmp_path):
    out = tmp_path / "stage_api"
    assert run(STAGE, "--service", "api", "--out", str(out)).returncode == 0
    files = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert {"apps/api/Dockerfile", "apps/api/requirements-railway.txt", "apps/api/enigma_api/main.py"} <= files
    assert not files & {"apps/api/lambda_handler.py", "apps/api/index.py", "apps/api/vercel.json", "apps/api/requirements-aws.txt"}
    assert (ROOT / "apps/api/lambda_handler.py").exists()           # staging never removes the AWS/Vercel fallback from the repo


def test_stager_refuses_an_output_directory_inside_the_repo_and_a_non_empty_one(tmp_path):
    assert run(STAGE, "--service", "web", "--out", str(ROOT / "docs" / "stage_x")).returncode == 2
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "keep.txt").write_text("x")
    assert run(STAGE, "--service", "web", "--out", str(busy)).returncode == 2
    assert (busy / "keep.txt").read_text() == "x"                    # nothing deleted or overwritten


def test_launcher_defaults_to_a_dry_run_that_makes_no_cloud_call(fake_repo, staged_web):
    r = deploy(fake_repo, staged_web)
    assert r.returncode == 0 and "DRY RUN" in r.stdout and "railway up" in r.stdout and str(staged_web) in r.stdout


@pytest.mark.parametrize("kw", [dict(project="00000000-0000-0000-0000-000000000000"), dict(env="staging")])
def test_launcher_refuses_any_other_project_or_environment(fake_repo, staged_web, kw):
    r = deploy(fake_repo, staged_web, **kw)
    assert r.returncode == 2 and "Refusing" in r.stderr


def test_launcher_refuses_an_upload_dir_inside_the_repo(fake_repo):
    r = deploy(fake_repo, fake_repo / "apps")
    assert r.returncode == 2 and "outside the repository" in r.stderr


def test_launcher_refuses_execute_without_the_exact_confirmation_phrase(fake_repo, staged_web):
    for extra in (("--execute",), ("--execute", "--confirm", "yes")):
        r = deploy(fake_repo, staged_web, *extra)
        assert r.returncode == 2 and "DEPLOY-ENIGMA-RAILWAY" in r.stderr


def test_launcher_refuses_the_account_wide_api_token(fake_repo, staged_web):
    import os
    env = {**os.environ, "RAILWAY_API_TOKEN": "x"}
    r = run(DEPLOY, "--project-dir", str(fake_repo), "--upload-dir", str(staged_web), "--railway-project-id", PROJECT_ID, "--service", "Enigma",
            "--execute", "--confirm", "DEPLOY-ENIGMA-RAILWAY", env=env)
    assert r.returncode == 2 and "RAILWAY_API_TOKEN" in r.stderr


def test_launcher_refuses_an_env_file_in_the_upload_dir(fake_repo, staged_web):
    (staged_web / "apps/web/.env.local").write_text("X=1")
    r = deploy(fake_repo, staged_web)
    assert r.returncode == 2 and ".env" in r.stderr
