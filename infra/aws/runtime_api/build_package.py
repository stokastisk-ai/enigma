"""Build the Lambda deployment zip for the optional Enigma runtime API (python3.13, arm64). Nothing is uploaded here.

    python3.13 infra/aws/runtime_api/build_package.py --out /path/to/runtime-api.zip

Contents: enigma_api (the unchanged API package), lambda_app.py, and the pinned pure-control-plane dependencies from apps/api/requirements.txt,
resolved for manylinux aarch64 / CPython 3.13 as binary wheels only. NOT included: psycopg (DynamoDB only), uvicorn, boto3/botocore (provided by
the Lambda runtime), and never torch / vllm / stokastisk / CUDA."""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
API = ROOT / "apps" / "api"
BANNED = ("torch", "vllm", "stokastisk", "nvidia", "triton", "psycopg", "uvicorn", "boto3", "botocore")


def requirements() -> list[str]:
    out = []
    for line in (API / "requirements.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-r"):
            assert not any(b in line.lower() for b in BANNED), line
            out.append(line)
    return out


def build(out: Path) -> str:
    with tempfile.TemporaryDirectory() as td:
        site = Path(td) / "pkg"
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-compile", "--target", str(site),
                        "--platform", "manylinux2014_aarch64", "--implementation", "cp", "--python-version", "3.13", "--only-binary=:all:",
                        "--index-url", "https://pypi.org/simple", *requirements()], check=True)
        bad = [p.name for p in site.iterdir() if any(p.name.lower().startswith(b) for b in BANNED)]
        assert not bad, f"banned packages resolved: {bad}"
        shutil.copytree(API / "enigma_api", site / "enigma_api", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(Path(__file__).with_name("lambda_app.py"), site / "lambda_app.py")
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(site.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts:
                    zi = zipfile.ZipInfo(str(p.relative_to(site)), date_time=(2026, 1, 1, 0, 0, 0))    # reproducible entries
                    zi.external_attr, zi.compress_type = 0o644 << 16, zipfile.ZIP_DEFLATED
                    z.writestr(zi, p.read_bytes())
    return hashlib.sha256(out.read_bytes()).hexdigest()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    digest = build(Path(a.out))
    print(Path(a.out).resolve(), Path(a.out).stat().st_size, "bytes sha256", digest)
