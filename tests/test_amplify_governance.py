"""Offline guards for the Amplify deployment-spec path (no AWS, no network). Real Amplify compatibility is NOT tested here."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
YML = ROOT / "infra" / "aws" / "amplify.yml"


def _app():
    return yaml.safe_load(YML.read_text())["applications"][0]


def test_generated_bundle_is_ignored_by_git_and_eslint():
    assert "/apps/web/.amplify-hosting/" in (ROOT / ".gitignore").read_text()
    assert ".amplify-hosting/**" in (ROOT / "apps/web/eslint.config.mjs").read_text()   # without it `npm run lint` scans compiled bundle code


def test_amplify_yml_monorepo_root_and_artifact_path():
    app = _app()
    assert app["appRoot"] == "apps/web"
    assert app["frontend"]["artifacts"]["baseDirectory"] == ".amplify-hosting"
    assert (ROOT / app["appRoot"] / "package-lock.json").exists()                          # npm ci needs it


def test_amplify_yml_env_handling_is_allowlisted_truncating_and_fails_closed():
    cmds = "\n".join(c for ph in _app()["frontend"]["phases"].values() for c in ph["commands"])   # executable commands only, not the explanatory comments
    assert "env | grep" not in cmds and "|| true" not in cmds.replace('printenv "$name" || true', "")
    block = _app()["frontend"]["phases"]["build"]["commands"][0]
    assert block.lstrip().startswith(": > .env.production")                                # truncate first, never accumulate
    assert "$'" not in block                                                               # bash-only quoting failed OPEN under dash
    names = re.search(r"for name in (.*?); do", block).group(1).split()
    assert all(not re.search(r"SECRET|PASSWORD|TOKEN|KEY|DSN", n) for n in names)
    assert "exit 1" in block


def test_amplify_yml_fails_fast_on_missing_runtimes():
    pre = " ".join(_app()["frontend"]["phases"]["preBuild"]["commands"])
    assert "[20,22,24]" in pre and "command -v python3.13" in pre


def test_env_block_refuses_hostile_values_under_posix_sh(tmp_path):
    block = _app()["frontend"]["phases"]["build"]["commands"][0]
    sh = tmp_path / "b.sh"
    sh.write_text(block)
    for value, ok in [("https://d1.cloudfront.net", True), ("a\nb=evil", False), ("has space", False), ('q"x', False), ("d$x", False), ("h#s", False)]:
        for shell in ("/bin/sh", "/bin/bash"):
            r = subprocess.run([shell, str(sh)], cwd=tmp_path, env={"PATH": "/usr/bin:/bin", "ENIGMA_PUBLIC_ORIGIN": value}, capture_output=True)
            assert (r.returncode == 0) == ok, (shell, value)


def test_no_account_ids_in_tracked_instruction_files():
    for f in ("skills.md", "CLAUDE.md", "docs/AWS_DEPLOYMENT_READINESS.md", "docs/AWS_AMPLIFY_NEXT16.md"):
        assert not re.search(r"(?<![\w.-])\d{12}(?![\w.-])", (ROOT / f).read_text()), f
