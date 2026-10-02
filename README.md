# Singular — Publisher golden table & MMP opportunities

Turns the five raw source tables (product + CRM) into two tables the go-to-market team can
use directly. The first is a **golden table** that answers "what do we know about this
publisher?". The second is a **ranked call list** of publishers that recently switched MMP or
are approaching a renewal.

| | |
|---|---|
| Apps / publishers | 734 / 300 |
| Publishers linked to a CRM account | 229 (76%) |
| Publishers on today's call list | 120 |

## How to run

SQL dialect: **DuckDB**, run against the CSVs in `data/`. Requires [uv](https://docs.astral.sh/uv/).

```bash
uv run main.py   # builds the tables and exports them to output/*.csv
uv run pytest    # data tests
```

"Today" is fixed at **2026-09-14**. Business thresholds live in `PARAMS` in `main.py`.

## The tables

**Two tables for the business:**

| Table | Grain |
|---|---|
| **`golden_apps`** | One row per app, with its publisher's performance, category, MMP, CRM account and owner. |
| **`publisher_opportunities`** | One row per publisher with a live signal (recent switch or upcoming renewal), ranked. |

**Behind them, eight internal steps** (cleaning and shared rules; not meant to be queried directly):

| Table | Grain |
|---|---|
| `stg_app_identification` | One row per app, with its publisher and cleaned matching keys. |
| `stg_sdk_installs` | One row per app × MMP install. |
| `stg_app_performance` | One row per app × platform (iOS / Android). |
| `stg_app_category` | One row per app. |
| `stg_crm_accounts` | One row per CRM account (duplicates included). |
| `int_app_mmp` | One row per app: current and previous MMP, switch date, next renewal. |
| `int_publisher_crm` | One row per publisher: its CRM account, duplicates merged. |
| `int_publisher_mmp` | One row per publisher: main MMP and MMP mix. |

Both main tables are also saved as CSVs in `output/`, ready to open in a spreadsheet.
"What do we know about this publisher?" is a single query:

```sql
select publisher_name, publisher_main_mmp, account_type, account_owner,
       string_agg(distinct app_category, ', ') as categories,
       count(*) as apps, sum(downloads) as downloads, sum(revenue) as revenue,
       sum(publisher_active_arr) as arr, min(next_renewal_date) as next_renewal
from golden_apps
where publisher_name = 'Petrel Studios'
group by all;
```

## Judgement calls

### 1. Ranking publishers

**Decision.** Downloads, users and revenue are each summed per publisher first. Each publisher
then gets a percentile rank on each metric (0 = smallest, 1 = largest), and the three are
averaged into `performance_score`. The list is sorted by that score, with revenue as the
tie-breaker.

**Why.**
- **No single metric tells the whole story.** Downloads are the volume an MMP measures and
  bills on. Revenue shows the budget to pay for it. Users show an active audience.
- **Percentiles stop one big number from taking over.** Raw revenue varies 13× between the
  median publisher and the largest, so ranking on raw values would mostly sort by revenue.
- **Easy to explain.** "Its average position across the three metrics."

**What it changes.** The three metrics usually agree: 9 of the top 10 are the same whichever
one you rank by. The score matters where they disagree. Zinc House is #2 by revenue but #12 by
downloads, so it lands at #7.

**Considered and rejected.**
- *Revenue only:* ignores volume.
- *Custom weights* (e.g. 50% revenue): there is no evidence yet for any particular weights.

Each metric's own rank is a column (`downloads_rank`, `users_rank`, `revenue_rank`), so a rep
can re-sort. The list is ranked by size, as the brief asks. Urgency is a filter on
`opportunity_type` and `days_to_renewal`, not part of the score.

### 2. Duplicate CRM accounts

Seven publishers have two CRM records, often disagreeing on owner or ARR. A publisher must
appear once, so the two records are merged.

**Decision.** The merge works **field by field**, instead of keeping one record and dropping
the other:

| Field | Rule | Why |
|---|---|---|
| Account id, name, status, territory | From the main record: the highest status (Customer > Partner > Churned > Prospect), then the highest ARR, then the most recent activity. A record someone already named "(duplicate)" never wins. | The record the business most likely treats as real. |
| ARR | The highest of the two | It's the same company entered twice, so adding them would double count. |
| Owner | From the most recently worked record that has an owner | Keeps the rep who is actually working the account. |

**Why.** Petrel Studios has a $179k record with **no owner**, and a second record owned by
**Viktor Costa** showing $0 ARR. Keeping either record whole loses something: an owner or
$179k. The field-by-field merge keeps both: a $179k customer owned by Viktor.

**Considered and rejected.**
- *Keep both records:* the publisher would appear twice.
- *Pick one whole record:* loses data, as above.
- *Sum the ARR:* double counts.

The merged-away records are listed in `crm_duplicate_ids`, so RevOps has a ready-made
clean-up list.

### 3. A publisher's main MMP

100 publishers use more than one MMP across their apps at the same time.

**Decision.** The main MMP is the one handling the **largest share of the publisher's
downloads**. Ties go to the MMP on the most apps, then the most recently adopted.

**Why.** An MMP measures installs, so download volume is what it handles, what it bills on,
and what a competing deal would be sized on. Counting apps instead lets several small apps
outweigh the flagship. Harbor Dynamics runs Branch on 3 apps, but its Kochava app carries 75%
of its downloads, so its main MMP is Kochava.

**What it changes.** This rule picks a different MMP than "most apps" for 13 of the 100
multi-MMP publishers, and a different one than "most revenue" for 12.

**Considered and rejected.**
- *Most apps:* can let small apps outweigh the flagship (Harbor Dynamics).
- *Most revenue:* an MMP doesn't measure revenue.
- *Most recent install:* the newest app isn't necessarily the important one.

`mmp_mix` shows the full split, e.g. `Kochava 75% (1 app), Branch 25% (3 apps)`, so a rep is
never misled by a single label. The rule lives in one place and feeds both tables
(`golden_apps.publisher_main_mmp` and `publisher_opportunities.main_mmp`).

### 4. Other decisions

| Topic | Decision | Why |
|---|---|---|
| **Golden table grain** | One row per app. Platform figures are columns (`ios_*`, `android_*`), and MMP history is current/previous columns. | Every number adds up under any filter. A row per platform or per MMP install would double count, e.g. revenue twice for the 108 apps with two MMPs. |
| **ARR in the golden table** | Filled only on the publisher's top app (`is_publisher_primary_row`), empty on its other apps | ARR belongs to the account. Copying it onto every app would count a 6-app publisher's ARR six times. With this rule, a plain `SUM` is always right. |
| **Current MMP & switches** | The latest install is the current MMP. A switch needs two or more installs. | There is no uninstall date. An app's first install is a new integration, not a lost deal. |
| **Renewal date** | The next anniversary of the current MMP's install | Contracts run 12 months and auto-renew. |
| **"Approaching renewal"** | Within **90 days**. The window runs from `renewal_window_opens` to `next_renewal_date`. | Time to reach out and close before the contract auto-renews. |
| **"Recently changed"** | Within **120 days** | The switch is still fresh. After that, the renewal is the next signal. |
| **Publisher's renewal** | The earliest renewal across its apps, with the main MMP's renewal alongside | Any open window gets a rep into the account. |
| **Publisher's switch** | Any app switched recently. The most recent switch fills `switched_from` → `switched_to`. | One app moving shows the publisher is shopping. |
| **CRM ↔ publisher link** | Cleaned website domain first. The name is used only when the website is missing or broken. No fuzzy matching. | A wrong match sends a rep to the wrong company, which is worse than a missed match. `crm_match_method` shows how each link was made. |
| **"Holdings" accounts** | Kept separate from the publisher with the similar name | They have their own domains. Three are Customers with $1.1M ARR and no publisher behind them, flagged for RevOps rather than guessed. |
| **Who is on the call list** | Any publisher with a switch or renewal signal. Publishers fully on Singular appear only when renewing. | A work list, not a directory (`golden_apps` is the directory). |
| **No CRM account / no owner** | Kept on the list (25 publishers with no CRM account, 27 rows with no owner) | Net-new leads. The rows with no owner are the routing queue for sales ops. |

**We are Singular, so the same signal means different things.** Each publisher gets one
`opportunity_type`, the most urgent first:

| `opportunity_type` | Meaning | Who acts | Today |
|---|---|---|---|
| `churned_from_singular` | An app recently left Singular | Account manager | 3 |
| `competitor_renewal` | A competitor contract renews within 90 days | Sales | 96 |
| `switched_to_competitor` | Recently moved between competitors | Sales | 13 |
| `switched_to_singular` | Moved an app to us; its other apps are elsewhere | Sales: expand | 5 |
| `singular_renewal` | Only Singular apps are renewing | Customer success | 3 |

### Data issues found and handled

| Issue | Handling |
|---|---|
| CRM websites in many formats (`HTTP://X.IO`, `www.x.com/`, `?utm_source=crm`, `N/A`) | Normalised to a plain domain. Placeholders become empty, so they never match each other. |
| `sdk_installs.mmp_installed` repeats each MMP's global total on every row | Dropped: summing it would be wrong. |
| Upstream type changes | Every column is cast explicitly, so a schema change breaks the build instead of corrupting data. |

## CRM sync: Airflow (`dags/crm_sync.py`)

A skeleton for Airflow 3, with placeholder connections.

```
ingestion DAGs ─▶ build tables ─▶ crm_sync: build payload ─▶ keep changed accounts ─▶ push in batches
```

- **Trigger and grain.** The sync runs when the tables are rebuilt (Airflow Assets), not on
  a timer. It pushes one record per CRM account, covering *all* linked accounts. That way,
  fields are cleared when a publisher leaves the call list.
- **No duplicates on retry.** The sync only **updates** existing accounts by their CRM id and
  never creates any, so a retry rewrites the same values. A small state table keeps a
  fingerprint of what was last sent to each account, and only changed accounts are pushed.
  That also catches changes caused by time passing, such as a renewal window opening.
- **Failure halfway through.** Each batch of 200 accounts is a separate task that records its
  success. A retry re-runs only the failed batches. If the retry fails too, the data team is
  alerted.
- **Reps' manual edits.** The sync writes only to its own `Product_*` fields, which are
  read-only for reps. Fields reps own (owner, stage, notes) are never touched.

Upstream ingestion should use a high-water mark (load rows in `updated_at` order and store
the last one loaded). That pattern doesn't fit this step: the sources have no `updated_at`,
and our outputs change with the date even when no source row changes.

## Testing

`uv run pytest` rebuilds everything from scratch and checks the **data**, not just the code
(26 tests):

- **Grain:** an app or publisher never appears twice, and no row is lost.
- **Totals match the source:** downloads, users, revenue and ARR add up to the same totals
  as the source. This catches a bad join that silently multiplies rows.
- **Completeness:** every publisher that qualifies is on the call list.
- **Business rules and known cases:** e.g. Petrel Studios keeps its $179k and its owner.

**In production, a silent break would be caught by:**
- **Running these tests before the CRM sync**, so bad data never reaches reps.
- **Comparing each run with the previous one**: row counts, the CRM match rate (76% today)
  and total ARR, with an alert on a large swing.
- **Freshness alerts** if a source stops updating.

## Assumptions

- **We are Singular:** a Singular app is our customer.
- **No uninstall date exists**, so the latest install is the current MMP.
- **Contracts start on the install date.**
- **Performance figures cover the same period** for every app, so they can be added together.
- **Duplicate CRM accounts are the same company**, so ARR is not added up.

### Questions asked, and the team's answers

| Question | Answer | Effect |
|---|---|---|
| Is 90 days the right renewal window? | Yes | None |
| Is 180 days right for "recently switched"? | Use 120 days | Call list went from 138 to 120 publishers |
| Which ranking metric does sales prefer? | Judgement call | Judgement call #1 |
| How should duplicate CRM accounts be resolved? | Judgement call | Judgement call #2 |
| Are "Holdings" accounts parents of publishers? | Use domains as the key | Kept separate |
| Can an app run two MMPs at once? | Part of the main-MMP rule | Judgement call #3 |

## What I'd do differently with more time

- **dbt with its self-hosted documentation site**, so anyone in the company can look up what
  every table and column means. That's what makes self-service analytics real.
- **A CRM clean-up list for RevOps:** the 21 CRM accounts with no publisher, the 7 duplicate
  merges, and the 11 linked accounts with no owner.
- **A prospecting list** of the 71 publishers with no CRM account.
- **Daily snapshots of the call list**, to measure which signals turn into won deals and tune
  the thresholds and the ranking with evidence.
- **Production hardening:** incremental loads and Iceberg `MERGE` at scale, and the real CRM
  client and state table wired into the DAG.