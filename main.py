"""Build the warehouse: run every SQL model in dependency order against DuckDB.

Each file in MODELS becomes a table named after the file, rebuilt from scratch on
every run (CREATE OR REPLACE), so the build is idempotent: re-running it can never
duplicate rows.
"""

from pathlib import Path

import duckdb

ROOT = Path(__file__).parent
SQL_DIR = ROOT / "sql"
OUTPUT_DIR = ROOT / "output"
DB_PATH = OUTPUT_DIR / "warehouse.duckdb"

# Business parameters live in one place so a changed threshold is a one-line diff.
PARAMS = {
    "data_dir": str(ROOT / "data"),
    "as_of_date": "2026-09-14",  # "today" per the brief, for reproducible results
}

# Dependency order: staging -> intermediate -> marts.
MODELS: list[str] = []

EXPORTS: list[str] = []


def set_params(con: duckdb.DuckDBPyConnection) -> None:
    for name, value in PARAMS.items():
        con.execute(f"SET VARIABLE {name} = ?", [value])
    con.execute("SET VARIABLE as_of_date = CAST(getvariable('as_of_date') AS DATE)")


def build(db_path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(exist_ok=True)
    con = duckdb.connect(str(db_path))
    set_params(con)
    for model in MODELS:
        query = (SQL_DIR / f"{model}.sql").read_text()
        table = Path(model).name
        con.execute(f"CREATE OR REPLACE TABLE {table} AS\n{query}")
        print(f"built {table:<32} {con.table(table).count('*').fetchone()[0]:>5} rows")
    for table in EXPORTS:
        con.execute(f"COPY {table} TO '{OUTPUT_DIR / table}.csv' (HEADER)")
    return con


if __name__ == "__main__":
    build().close()
