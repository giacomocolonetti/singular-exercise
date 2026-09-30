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

### MMP history, switches and renewals (`int_app_mmp`, one row per app)

| Concept | Rule | Why |
|---|---|---|
| Current MMP | The app's most recent install | There is no uninstall date. 108 apps have two installs and we assume the newer one replaced the older. |
| Switch | The app has ≥ 2 installs; switch date = latest install | An app's first-ever install is a new integration, not a lost or won deal. |
| Next renewal | Next yearly anniversary of the current install, on or after today (and never the install day itself) | Contracts run 12 months and auto-renew. 29 Feb installs renew on 28 Feb. |
| **Approaching renewal** | Renewal within **90 days** | Enough time to reach out, run a trial and close before the auto-renewal locks the publisher in for another year. |
| **Recently changed** | Switch within the last **180 days** | Six months in, a publisher is still judging the switch and is often still moving its other apps. After that, the next useful signal is the renewal, which the 90-day rule catches. |

Both thresholds are parameters in `main.py`, and both are among the questions sent to the team.

### Linking CRM accounts to publishers (`int_publisher_crm`, one row per publisher)

The CRM and the product data share no key, so each CRM account is matched in two steps:

1. **Normalised domain** (website ↔ `publisher_domain`): 216 publishers.
2. **Normalised name** as a fallback, only for accounts that didn't match on domain (missing
   website, or a typo such as `orbitd-ynamics.com`): 13 more publishers.

`crm_match_method` (`domain` / `name` / `unmatched`) is kept as a column so users can see how
each link was made. **71 publishers have no CRM account.** They stay in the tables with an
empty owner: they are net-new leads, not errors.

**No fuzzy matching.** Pointing a rep at the wrong account is worse than a missed match.
Example: `Brightfin Holdings` (a Customer, $408k ARR) looks like publisher `Brightfin` but has
its own domain. It may be a parent company or a different business, and guessing is not our
call. Three such "Holdings" Customers hold **$1.1M ARR (8% of the total)** that cannot be
linked to a publisher today. This is an open question sent to the team.

**Duplicate CRM accounts** (7 publishers have two records, often with conflicting owners).
Instead of picking one record whole, each field is resolved on its own:

| Field | Rule | Why |
|---|---|---|
| `crm_id`, name, type, territory | Main record: never one a human already named "(duplicate)", then the highest status (Customer > Partner > Churned > Prospect), then the highest ARR, then the most recent activity | Keeps the record the business most likely treats as real |
| `active_arr` | Max across the records | Same account entered twice: summing would double count |
| `account_owner` | From the most recently worked record that has an owner | Petrel Studios: the $179k record has no owner, the other has Viktor Costa. Taking the whole record would leave a customer with no owner. |
| `crm_duplicate_ids` | The other record ids | A ready-made merge list for RevOps |
