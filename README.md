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

## The tables

| Table | Grain (one line) |
|---|---|
| `golden_apps` | One row per app: identity, category, performance, MMP history and its publisher's CRM account. |
| `publisher_opportunities` | One row per publisher with a live signal (recent switch or renewal in window), ranked for reps to work top-down. |

Both marts are also exported to `output/*.csv`, so they open directly in a spreadsheet.

### `golden_apps`: why one row per app

One flat table with no joins needed: filter and aggregate it like a spreadsheet. The hard part
is choosing a grain where **every number can be summed under any filter without double
counting**.

An app has two independent child lists: performance per platform (iOS/Android) and MMP
installs over time. Any grain below the app that includes both multiplies rows. For example,
app × platform × MMP counts revenue twice for the 108 apps with two MMPs, and nothing in the
table warns you. So:

| Option | Rows | Verdict |
|---|---|---|
| **App** (chosen) | 734 | Every measure adds up. Platform detail kept as `ios_*` / `android_*` columns; MMP history as `current_*` / `previous_*` columns plus a readable `mmp_history`. |
| App × platform | 1,395 | Measures still add up, but "how many apps are on Adjust" needs `COUNT DISTINCT`. A spreadsheet user would get it wrong. |
| App × MMP install | 842 | Double counts performance. Rejected. |

**Pros:** easy to count (one row = one app); safe to sum; still drills down to platform level.
**Cons:** MMP history is limited to current + previous as columns. That covers all the data
today (max two installs per app), and `mmp_count` / `mmp_history` show when an app has more.
A per-platform time series would need its own table.

**Publisher ARR is the one number that can't be copied onto every row.** It belongs to the
account, not the app, so repeating it would count a 6-app publisher's ARR six times in any
pivot. `publisher_active_arr` is therefore filled only on the publisher's top app by revenue
(`is_publisher_primary_row = true`) and is empty on its other apps. A plain `SUM` gives the
right answer under any filter, and the flag makes it explicit which row carries the value.
Text attributes (owner, territory, account type) do repeat on every row, because they are
never summed.

### `publisher_opportunities`: the call list

A rep works an account, not an app, so this table has **one row per publisher**, and it
is built from `golden_apps` so both tables always show the same numbers. It holds 137
publishers today. Deciding how to represent a multi-app publisher in one row:

| Question | Decision | Why |
|---|---|---|
| Who is on the list? | Publishers with a recent switch **or** a renewal within 90 days, excluding publishers entirely on Singular | It's a work list, not a directory (`golden_apps` is the directory). A publisher fully on Singular is our customer, not a lead. |
| Main MMP | The current MMP handling the **largest share of the publisher's downloads**, then the most apps, then the most recent adoption | An MMP attributes installs, so download volume is what it handles and what it bills on. App count alone would let three tiny apps outweigh the flagship. `mmp_mix` shows the full split, e.g. `Kochava 75% (1 app), Branch 25% (3 apps)`. |
| Which renewal date | `next_renewal_date` = the **earliest** upcoming renewal across all the publisher's apps; `main_mmp_next_renewal_date` is kept alongside | Any open window gets a rep into the conversation. Winning one small app is how you land the account. |
| Which switch | Any app switched within 180 days. The most recent one fills `switched_from` → `switched_to`, and `apps_switched_recently` counts them | One app moving is already a signal that the publisher is shopping. |
| Ranking | `performance_score` = the average of the percentile ranks of downloads, users and revenue (each summed to the publisher first) | Percentiles put the three metrics on one scale, so one outlier can't dominate. Each metric's own rank is also a column, so a rep can re-sort. |

**We are Singular, so the same signal means different things.** Each row gets exactly one
`opportunity_type`, the most urgent first:

| `opportunity_type` | Meaning | Who acts |
|---|---|---|
| `churned_from_singular` | An app recently moved **away from us** | Account manager: save it |
| `competitor_renewal` | A competitor contract is renewing within 90 days | Sales: open window to win |
| `switched_to_competitor` | Recently moved between competitors: evaluating, in motion | Sales |
| `switched_to_singular` | Recently moved an app **to us**; its other apps are elsewhere | Sales: expand to the rest |
| `singular_renewal` | Only our own apps are renewing | Customer success: retention |

Publishers with **no CRM account** (28 on the list) and **Churned** accounts stay in: they are
net-new and win-back leads. An empty `account_owner` (30 rows) is the routing queue for sales ops.

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

## Airflow: CRM sync (`dags/crm_sync.py`)

A skeleton for Airflow 3; the CRM and warehouse connections are placeholders.

```
ingestion DAGs ──asset──▶ build_marts ──asset──▶ crm_sync:  build_payload ─▶ diff_against_sync_state ─▶ chunk ─▶ push_batch ×N
```

**What triggers a sync, and at what grain.** Data-aware scheduling with Airflow **Assets**.
Each ingestion DAG declares its raw table as an outlet. `build_marts` is scheduled on those
assets and emits `golden_apps` / `publisher_opportunities`, and `crm_sync` runs when both
marts are updated. No cron guessing: the sync runs because the data changed, and only after
the build succeeded. The grain is **one CRM account (`crm_id`) per record**, because the CRM
account is what a rep works. The payload covers *every* matched account, not only those on
today's call list: when a publisher leaves the list, its CRM fields must be cleared, or the
CRM keeps showing an expired "renewal approaching".

**How retries avoid duplicates.** Two layers:
1. *The CRM write is an update by `crm_id`, never an insert.* Sending the same record twice
   writes the same values twice, which is harmless. The sync never creates accounts (the 71
   unmatched publishers reach sales ops through the marts instead), so it cannot create
   duplicate accounts.
2. *`ops.crm_sync_state`* holds one row per CRM account (about 230 rows, not a copy of the
   data) with the hash of the last payload pushed. Only records whose hash changed get
   sent. This saves CRM API quota and avoids touching `LastModifiedDate` for nothing. It is
   also the only way to catch changes that come from **time passing** (a renewal window
   opening) rather than from new source rows.

Why not a high watermark on the sources, the usual pattern for *ingestion*? It is right
there (paginate by `updated_at`, oldest first, and store the watermark per table, so a broken
run resumes where it stopped), and that is how the upstream ingestion DAGs should work. It
doesn't fit this step: the sources are full snapshots with no `updated_at`, and our outputs
change as dates move even when no source row does. Similarly, Iceberg `MERGE` gives
idempotent writes *inside the lake*, but the duplicate risk here is on the CRM side. At
this size, rebuilding the marts from scratch (`CREATE OR REPLACE`) is already idempotent.

**What happens if the run fails halfway.** The push is split into **mapped tasks, one per
batch** of 200 accounts. Each batch is all-or-none in the CRM, and only after the CRM accepts
it does the batch write its rows to `crm_sync_state`. If batch 4 of 6 fails, Airflow retries
only batch 4. A later full re-run skips batches 1–3 because their hashes already match. A
task that is retried once and fails again triggers `on_failure_callback`, which alerts the
data engineering team (Slack/pager) with a link to the logs. `max_active_runs=1` prevents
two runs from racing.

**How we avoid overwriting a rep's manual edits.** Product facts and rep judgement live in
**separate fields**. The sync writes only its own `Product_*__c` fields (`FIELD_MAP`), which
are read-only for reps through field-level security. The fields reps own (owner, stage,
notes) are never in the payload, so there is nothing to overwrite. If a field ever has to be
shared, the fallback is to read the CRM field history before pushing, and skip and flag any
field a person edited after our last push.

## Testing

`uv run pytest` builds a fresh warehouse in a temp directory and checks the data itself, not
just the code. Three kinds of tests:

- **Grain:** the table's key is unique, and no row is lost or invented relative to the source.
- **Reconciliation:** totals equal the source exactly (downloads, users, revenue, ARR), per-platform
  columns add up to the totals, and ARR sits on exactly one row per publisher. These are the tests
  that catch a join that silently fans out.
- **Business rules and known cases:** e.g. a single install is never a switch; APP-1001 moved
  AppsFlyer → Adjust on 2026-07-02; Petrel Studios keeps both its $179k ARR and its owner.
- **Completeness:** the eligible publishers are recomputed independently from `golden_apps`,
  and every one of them must be on the call list. A filter or join that quietly drops
  publishers fails here, even though the output still "looks fine".

**Catching a silent break in production:**

- **Tests as a gate, not a report.** The same checks run inside `build_marts` *before* it
  emits its assets. A table that fails a test never triggers the CRM sync, so bad data never
  reaches reps.
- **Run-over-run checks:** row counts, the matched-CRM share (76% today), the number of
  publishers on the call list, and total ARR, each compared with the previous run and
  alerting on large swings. A sudden drop in the match rate usually means the CRM changed how
  it formats websites.
- **Freshness:** alert if a source asset hasn't updated within its expected interval, so a
  stalled ingestion doesn't leave reps working a stale list.
- **CI:** the build and tests run on every pull request against the committed sample data.

## Assumptions

Where the brief was ambiguous, I decided and wrote it down here. The ones that change what a
rep sees are also sent to the team as questions.

- **We are Singular.** Singular is one of the MMPs in the data, so a Singular app is our
  customer, not a competitor's.
- **The latest install is the current MMP.** There is no uninstall date. Two installs = a
  switch, not two MMPs running in parallel.
- **Contracts start on the install date** of the current MMP and renew on its anniversary.
- **Performance figures are comparable across apps and platforms** (same period), so they
  can be summed across iOS and Android and across a publisher's apps.
- **The CRM website identifies the company** and matches `publisher_domain`. The name is
  only a fallback.
- **Duplicate CRM accounts are the same company entered twice**, so ARR is the max, never
  the sum.
- **A publisher fully on Singular is not a lead.** It is left off the call list but stays in
  `golden_apps`.
- **"Today" is 2026-09-14**, and a renewal falling exactly on today is still in the window.

### Open questions sent to the team (2026-09-30)

1. Is 90 days the right renewal window for the real sales cycle?
2. Is 180 days the right definition of "recently switched"?
3. Does sales prefer one metric (e.g. revenue) over the combined performance score?
4. Is there an existing rule for which duplicate CRM account is the real one?
5. Are the "X Holdings" accounts parent companies of publisher "X"? (Three Customers, $1.1M ARR.)
6. Do publishers ever run two MMPs on the same app in parallel?

Each answer is a one-line change: a parameter in `main.py` or a single rule in one SQL file.

## What I'd do next

- **dbt.** Move the models to dbt: tests become declarations next to each model, and lineage
  comes for free. Most importantly for the company, dbt generates a **documentation site we
  can self-host**, listing every table and column with its description, grain, tests and
  lineage. Anyone in sales ops, RevOps or finance can then find what a column means without
  asking an engineer. That's what makes self-service analytics real, not just possible.
- **A CRM-hygiene table for RevOps:** the 21 CRM accounts that match no publisher (Holdings
  accounts, `.example` domains), the 7 duplicate merge lists, and the 11 matched accounts
  with no owner. Today it's a manual clean-up nobody owns.
- **A whitespace list:** the 71 publishers with no CRM account, ranked the same way. That is
  a prospecting list for net-new business.
- **Daily snapshots of the call list**, so we can measure which signals actually turn into
  won deals, and tune the 90/180-day thresholds and the ranking with data instead of intuition.
- **Parent/child accounts** in the golden table if the team confirms the Holdings structure.
- **At scale:** incremental models and Iceberg `MERGE` for the lake tables. Wire the real CRM
  client, the `crm_sync_state` table and the field-history guard into the DAG, and add a
  `build_marts` DAG that runs the tests as a gate.
