"""Runs db/migrations + the job/isolation/privilege assertions on a throwaway local Postgres (skipped when PostgreSQL binaries are absent)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "sql"))
import run_sql_tests  # noqa: E402


@pytest.mark.skipif(run_sql_tests.find_pg_bin() is None, reason="PostgreSQL server binaries not installed")
def test_portable_schema_and_privilege_assertions():
    ok, log = run_sql_tests.run_all()
    assert ok, log[-3000:]
    assert "SQL checks passed" in log
