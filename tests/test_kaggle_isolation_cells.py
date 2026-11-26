"""The isolation cells (docs/kaggle/enigma_isolation_cells.md) are copy-pasted into Kaggle: they must parse, carry no credential and only use real helpers."""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "docs" / "kaggle" / "enigma_isolation_cells.md").read_text()
BLOCKS = re.findall(r"```python\n(.*?)```", TEXT, flags=re.S)


def test_every_cell_parses_and_all_isolation_cells_exist():
    for b in BLOCKS:
        ast.parse(b)
    for name in ("## P.", "## R.", "## Z0.", "## Z1.", "## A.", "## B.", "## C.", "## D.", "## E.", "## F.", "## G."):
        assert name in TEXT


def test_cells_never_print_or_embed_the_key_and_never_pass_it_on_a_command_line():
    assert not re.search(r"enig_rt_[a-z0-9]{8}_[A-Za-z0-9_-]{10,}", TEXT)
    assert not re.search(r"print\([^)]*(token|secret|get_secret)", TEXT, re.I)
    assert not re.search(r"mark\([^)]*(get_secret|token_getter\(\))", TEXT)
    assert "--token" not in TEXT


def test_cells_use_only_real_connector_helpers():
    from enigma_connector import stage_runner, supervise
    assert all(hasattr(stage_runner, n) for n in ("run_stage", "MarkerLog")) and hasattr(supervise, "ConnectorProcess")
    for stage in re.findall(r'run_stage\("([a-z-]+)"', TEXT):
        assert stage in stage_runner.DEFAULT_DURATION


def test_cpu_only_cells_do_not_touch_the_gpu_or_enigma():
    by_head = dict(re.findall(r"## ([A-Z0-9]+)\. .*?```python\n(.*?)```", TEXT, flags=re.S))
    for cell in ("P", "Z0", "A", "B", "F", "G"):
        assert not re.search(r"pynvml|NvmlSampler|run_stage|torch|vllm", by_head[cell]), cell
    for cell in ("P", "Z0", "B", "F", "G"):
        assert "railway.app" not in by_head[cell], cell


def test_the_pinned_revision_contains_the_marker_log():
    rev = re.search(r'REV = "([0-9a-f]{7,40}|PINNED_REVISION)"', TEXT).group(1)
    if rev == "PINNED_REVISION":
        pytest.skip("revision not pinned yet")
    if subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{rev}^{{commit}}"], capture_output=True).returncode != 0:
        pytest.skip(f"revision {rev} is not in this clone's history")
    src = subprocess.run(["git", "-C", str(ROOT), "show", f"{rev}:connector/enigma_connector/stage_runner.py"], capture_output=True, text=True).stdout
    assert "class MarkerLog" in src
