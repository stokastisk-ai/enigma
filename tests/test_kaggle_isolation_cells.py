"""docs/kaggle/enigma_isolation_cells.md is now an index of the generated notebooks; it must point at all six and carry no runnable cells."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "docs" / "kaggle" / "enigma_isolation_cells.md").read_text()
NOTEBOOKS = ("enigma_kaggle_cpu_platform_baseline", "enigma_kaggle_cpu_connector_isolation", "stokastisk_kaggle_gpu_platform_baseline",
             "stokastisk_kaggle_vllm_stability", "enigma_kaggle_registration_without_vllm", "enigma_stokastisk_qwen25_05b_final_integration")


def test_index_lists_all_six_notebooks_and_they_exist():
    for n in NOTEBOOKS:
        assert f"{n}.ipynb" in TEXT
        assert (ROOT / "docs" / "kaggle" / "diagnostics" / f"{n}.ipynb").is_file()


def test_index_has_no_runnable_cells_and_no_credentials():
    assert "```python" not in TEXT
    assert not re.search(r"enig_rt_[a-z0-9]{8}_[A-Za-z0-9_-]{10,}", TEXT)


def test_index_states_the_methodology_correction():
    assert "not** a CPU-only test" in TEXT and "PASS | FAIL | INCONCLUSIVE" in TEXT and "Missing files do not prove" in TEXT
