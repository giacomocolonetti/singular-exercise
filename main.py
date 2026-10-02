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
    "as_of_date": "2026-09-14",  # "today" per the brief, for reproducible results
    "renewal_window_days": 90,  # renewal this close = reachable before the auto-renewal locks in
    "recent_switch_days": 120,  # switch this recent = publisher still judging its new MMP
}

# Dependency order: staging -> intermediate -> marts.
MODELS: list[str] = [
    "staging/stg_app_identification",
    "staging/stg_sdk_installs",
    "staging/stg_app_performance",
    "staging/stg_app_category",
    "staging/stg_crm_accounts",
    "intermediate/int_app_mmp",
    "intermediate/int_publisher_crm",
    "intermediate/int_publisher_mmp",
    "marts/golden_apps",
    "marts/publisher_opportunities",
]

EXPORTS: list[str] = ["golden_apps", "publisher_opportunities"]


def set_params(con: duckdb.DuckDBPyConnection) -> None:
    for name, value in PARAMS.items():
        con.execute(f"SET VARIABLE {name} = ?", [value])
    con.execute("SET VARIABLE as_of_date = CAST(getvariable('as_of_date') AS DATE)")


def build(db_path: Path = DB_PATH, export: bool = True) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(f"SET file_search_path = '{ROOT / 'data'}'")  # read_csv('x.csv') resolves to data/x.csv
    set_params(con)
    con.execute((SQL_DIR / "macros.sql").read_text())
    for model in MODELS:
        query = (SQL_DIR / f"{model}.sql").read_text()
        table = Path(model).name
        con.execute(f"CREATE OR REPLACE TABLE {table} AS\n{query}")
        print(f"built {table:<32} {con.table(table).count('*').fetchone()[0]:>5} rows")
    for table in EXPORTS if export else []:
        con.execute(f"COPY {table} TO '{OUTPUT_DIR / table}.csv' (HEADER)")
    return con


if __name__ == "__main__":
    build().close()
