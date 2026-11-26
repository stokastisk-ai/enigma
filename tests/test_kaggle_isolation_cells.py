"""docs/kaggle/enigma_isolation_cells.md indexes the three generated notebooks; no runnable cells, no credentials, no build 6."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "docs" / "kaggle" / "enigma_isolation_cells.md").read_text()


def test_index_lists_the_three_notebooks_and_they_exist():
    for n in ("01_enigma_kaggle_platform_aws_diagnostics", "02_stokastisk_build5_kaggle_t4_validation", "03_enigma_stokastisk_build5_aws_integration"):
        assert f"{n}.ipynb" in TEXT and (ROOT / "docs" / "kaggle" / "diagnostics" / f"{n}.ipynb").is_file()


def test_index_is_build5_only_without_cells_or_credentials():
    assert "```python" not in TEXT and "build 5" in TEXT
    assert not re.search(r"(enig_rt|edx)_[a-z0-9]{8}_[A-Za-z0-9_-]{10,}", TEXT)
