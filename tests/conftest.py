import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from main import build  # noqa: E402


@pytest.fixture(scope="session")
def con(tmp_path_factory):
    """A freshly built warehouse, so tests always check the current SQL, not a stale file."""
    connection = build(tmp_path_factory.mktemp("warehouse") / "test.duckdb", export=False)
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def scalar(con):
    return lambda sql: con.sql(sql).fetchone()[0]