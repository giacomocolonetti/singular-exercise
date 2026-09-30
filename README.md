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

## Model layers

```
data/*.csv ──▶ staging/       type, trim and build join keys; one model per source, same grain as the source
           ──▶ intermediate/  business logic that is reused (MMP history per app, CRM ↔ publisher match)
           ──▶ marts/         the tables people query
```

Every model is rebuilt from scratch with `CREATE OR REPLACE TABLE` on each run, so a re-run
can never duplicate rows.

### Staging: what gets cleaned and why

- **Explicit casts.** Sources are read as text and cast column by column, so a schema change
  upstream fails the build loudly instead of silently changing a type.
- **One normalisation per concept, used on both sides** (`sql/macros.sql`):
  - `normalize_domain`: CRM websites arrive as `HTTP://COBALTPLAY.IO`, `www.x.com/`,
    `https://x.com?utm_source=crm`, `  x.com  `. All become `x.com`. Placeholders (`N/A`,
    `n/`, `none`) become NULL, so they can never match each other.
  - `normalize_company_name`: lowercase, punctuation removed, `(duplicate)` and legal
    suffixes (Inc, LLC, Ltd, GmbH, Corp) stripped. `Holdings` is kept on purpose (see matching).
- **`sdk_installs.mmp_installed` is dropped.** It is the global install count of that MMP
  repeated on every row (336 on every AppsFlyer row), not a per-app fact, so summing it gives
  nonsense.
