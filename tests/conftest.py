"""Two ways to run the same data tests.

* Default (development, CI): build a fresh warehouse from data/ as of the brief's
  2026-09-14 and test it, so tests always check the current SQL.
* Gate (production): `WAREHOUSE_PATH=... pytest -m "not fixture_data"` tests the warehouse
  a pipeline has just built, before anything downstream reads it. Tests marked
  `fixture_data` pin known cases of the sample data and are skipped there.
"""

import os
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from main import build, set_params  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "fixture_data: pins a known case of the committed sample data")


@pytest.fixture(scope="session")
def con(tmp_path_factory):
    if warehouse := os.environ.get("WAREHOUSE_PATH"):
        connection = duckdb.connect(warehouse, read_only=True)
        as_of = connection.sql("select max(as_of_date)::varchar from golden_apps").fetchone()[0]
        set_params(connection, as_of)  # same thresholds as the build, which session variables don't keep
    else:
        connection = build(tmp_path_factory.mktemp("warehouse") / "test.duckdb", export=False)
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def scalar(con):
    return lambda sql: con.sql(sql).fetchone()[0]
