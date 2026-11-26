"""Copy the generated Kaggle notebooks (01, 02, 03; build 5 only) (docs/kaggle/diagnostics/) into the directory you run Kaggle notebooks from.

    python3.13 scripts/sync_kaggle_notebooks.py --dest /path/to/notebooks
    ENIGMA_KAGGLE_NOTEBOOK_DIR=/path/to/notebooks python3.13 scripts/sync_kaggle_notebooks.py

The destination is never hard-coded. A destination file that differs from the source is first preserved as ``<name>.ipynb.<UTC>.bak`` (never deleted),
then replaced. Every copied notebook is validated (nbformat schema when available, Python syntax of every code cell, no stored outputs, no credential),
and its absolute path and SHA-256 are printed. Files in the destination that this script does not manage (e.g. executed notebooks) are never touched."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "docs" / "kaggle" / "diagnostics"
NAMES = ("01_enigma_kaggle_platform_aws_diagnostics", "02_stokastisk_build5_kaggle_t4_validation", "03_enigma_stokastisk_build5_aws_integration")
_KEY = re.compile(r"(?:enig_rt|edx)_[a-z0-9]{8}_[A-Za-z0-9_-]{10,}|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")     # runtime key, diagnostic key, AWS key id


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(path: Path) -> None:
    """Raise ValueError if the notebook is not a clean, runnable diagnostic notebook."""
    raw = path.read_text(encoding="utf-8")
    nb = json.loads(raw)
    try:
        import nbformat
        nbformat.validate(nbformat.reads(raw, as_version=4))
    except ImportError:
        if nb.get("nbformat") != 4:
            raise ValueError(f"{path.name}: not nbformat 4") from None
    for i, c in enumerate(nb["cells"]):
        if c["cell_type"] == "code":
            if c.get("outputs") or c.get("execution_count") is not None:
                raise ValueError(f"{path.name}: cell {i} carries execution output")
            ast.parse("".join(c["source"]), filename=f"{path.name}#cell{i}")
    if _KEY.search(raw):
        raise ValueError(f"{path.name}: contains something that looks like a credential")


def sync(dest: Path, *, dry_run: bool = False) -> list[dict]:
    if not dest.is_dir():
        raise SystemExit(f"destination {dest} does not exist (create it first)")
    rows = []
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    for name in NAMES:
        src, dst = SRC / f"{name}.ipynb", dest / f"{name}.ipynb"
        validate(src)
        action = "unchanged"
        if not dst.exists():
            action = "created"
        elif sha256(dst) != sha256(src):
            action = "updated"
            backup = dest / f"{name}.ipynb.{stamp}.bak"
            if not dry_run:
                shutil.copy2(dst, backup)                                   # never lose the previous version
        if action != "unchanged" and not dry_run:
            tmp = dst.with_suffix(".ipynb.tmp")
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)
        if not dry_run:
            validate(dst)
            assert sha256(dst) == sha256(src), f"{dst} does not match its source after copying"
        rows.append({"path": str(dst.resolve()), "sha256": sha256(src), "action": action})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", default=os.environ.get("ENIGMA_KAGGLE_NOTEBOOK_DIR"), help="target directory (or set ENIGMA_KAGGLE_NOTEBOOK_DIR)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if not a.dest:
        ap.error("give --dest or set ENIGMA_KAGGLE_NOTEBOOK_DIR")
    for r in sync(Path(a.dest).expanduser(), dry_run=a.dry_run):
        print(f"{r['action']:9s} {r['sha256']}  {r['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
