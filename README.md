# Singular — Publisher golden table & MMP opportunities

Turns five raw source tables (product side + CRM) into tables the go-to-market team can
query directly, without re-deriving joins.

## How to run

Requires [uv](https://docs.astral.sh/uv/). SQL dialect: **DuckDB**, run against the CSVs in `data/`.

```bash
uv run main.py   # builds output/warehouse.duckdb and exports the marts to output/*.csv
uv run pytest    # data tests: grain, reconciliation, business rules
```

"Today" is fixed at **2026-09-14** (as the brief asks) so results are reproducible; every
business parameter lives in `PARAMS` in `main.py`.
