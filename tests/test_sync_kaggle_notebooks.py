"""scripts/sync_kaggle_notebooks.py: configurable destination, never loses a differing file, validates what it copies (CPU only)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("syncnb", ROOT / "scripts" / "sync_kaggle_notebooks.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


def test_creates_then_reports_unchanged(tmp_path):
    rows = sync.sync(tmp_path)
    assert [r["action"] for r in rows] == ["created"] * 6
    assert all(Path(r["path"]).is_file() and sync.sha256(Path(r["path"])) == r["sha256"] for r in rows)
    assert [r["action"] for r in sync.sync(tmp_path)] == ["unchanged"] * 6


def test_a_differing_destination_file_is_backed_up_before_it_is_replaced(tmp_path):
    sync.sync(tmp_path)
    target = tmp_path / "enigma_kaggle_cpu_platform_baseline.ipynb"
    target.write_text('{"my": "edited copy"}')
    rows = {Path(r["path"]).name: r["action"] for r in sync.sync(tmp_path)}
    assert rows["enigma_kaggle_cpu_platform_baseline.ipynb"] == "updated"
    backups = list(tmp_path.glob("enigma_kaggle_cpu_platform_baseline.ipynb.*.bak"))
    assert len(backups) == 1 and backups[0].read_text() == '{"my": "edited copy"}'


def test_unmanaged_files_are_never_touched(tmp_path):
    keep = tmp_path / "enigma-stokastisk-qwen25-05b-kaggle (3).ipynb"
    keep.write_text("executed notebook")
    sync.sync(tmp_path)
    assert keep.read_text() == "executed notebook"


def test_destination_is_required_and_never_hard_coded(monkeypatch):
    monkeypatch.delenv("ENIGMA_KAGGLE_NOTEBOOK_DIR", raising=False)
    with pytest.raises(SystemExit):
        sync.main([])
    assert "/media/" not in (ROOT / "scripts" / "sync_kaggle_notebooks.py").read_text()


def test_dry_run_changes_nothing(tmp_path):
    sync.sync(tmp_path, dry_run=True)
    assert list(tmp_path.iterdir()) == []
