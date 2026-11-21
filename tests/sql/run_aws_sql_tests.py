"""Apply infra/aws/sql (portable PostgreSQL) to a THROWAWAY local Postgres cluster and run the job/isolation/privilege assertions.

Same harness as run_sql_tests.py (own temp cluster on a unix socket, removed afterwards). Never touches any existing database.
    python3.13 tests/sql/run_aws_sql_tests.py
This is local PostgreSQL evidence, NOT evidence about an RDS instance.
"""
from __future__ import annotations

import sys

from run_sql_tests import ROOT, SQL_DIR, run_all

STEPS = [SQL_DIR / "aws_shim.sql", *sorted((ROOT / "infra" / "aws" / "sql").glob("*.sql")), SQL_DIR / "test_aws_jobs.sql"]

if __name__ == "__main__":
    ok, log = run_all(STEPS)
    print(log)
    sys.exit(0 if ok else 1)
