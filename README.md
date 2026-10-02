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

"Today" defaults to **2026-09-14**, as the brief asks. `--as-of YYYY-MM-DD` sets another date.
Business thresholds live in `PARAMS` in `main.py`.

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

**Decision.** The three metrics are summed per publisher, then combined into **two signals,
weighted 50/50**:

```
performance_score = 50% × volume + 50% × revenue
  volume  = average percentile of downloads and users
  revenue = percentile of revenue        (percentile: 0 = smallest on the list, 1 = largest)
```

**Why two signals, not three.** Downloads and users are almost the same number
(correlation 0.99). Averaging three metrics would quietly count volume twice, giving it ⅔ of
the weight. So they are averaged into one volume signal, and the weighting is stated outright.

**Why 50/50.** Volume is what an MMP measures and bills on. Revenue is the budget the publisher
has to pay for it. A good deal needs both, and no outcome data yet says which matters more.
The weight is one parameter in `main.py` (`ranking_volume_weight`), ready to tune once won
deals can be measured.

**Why percentiles.** Raw revenue varies 13× between the median publisher and the largest, so
raw values would let a few giants take over.

**What it changes.** The metrics agree at the top (9 of the top 10 are the same as a
revenue-only ranking). The weighting matters in the middle. Beacon Software is #10 by
downloads but #36 by revenue. Counting volume twice put it at #14; it now sits at #21.

**Considered and rejected.**
- *Averaging the three metrics:* counts volume twice, as above.
- *Revenue only:* ignores the volume an MMP actually handles.
- *Volume only:* ignores the ability to pay.

`volume_percentile`, `revenue_percentile` and each metric's own rank are columns, so a rep can
see why a publisher ranks where it does, or re-sort. The list is ranked by size, as the brief asks. Urgency is a filter on `signal`,
`sales_motion` and `days_to_renewal`, not part of the score.

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
| **ARR in the golden table** | Two columns. `publisher_active_arr` sits only on the publisher's top app. `publisher_arr_repeated` sits on every app. | ARR belongs to the account, not the app. No single column can be both safe to sum and correct under any app filter (see below). |
| **Current MMP & switches** | The latest install is the current MMP. A switch needs two or more installs. | There is no uninstall date. An app's first install is a new integration, not a lost deal. |
| **Renewal date** | The next anniversary of the current MMP's install | Contracts run 12 months and auto-renew. |
| **"Approaching renewal"** | Within **90 days**. The window runs from `renewal_window_opens` to `next_renewal_date`. | Time to reach out and close before the contract auto-renews. |
| **"Recently changed"** | Within **120 days** | Switches happen at about 8 a month, so this threshold mostly sets the **size** of the list (180 days gave 138 publishers, 120 gives 120). It is a capacity setting for the sales team more than a measure of signal quality. |
| **Publisher's renewal** | The earliest renewal across its apps, with the main MMP's renewal alongside | Any open window gets a rep into the account. |
| **Publisher's switch** | Any app switched recently. The most recent switch fills `switched_from` → `switched_to`. | One app moving shows the publisher is shopping. A switch to a competitor means a new 12-month contract was just signed, so the pitch is not to undo it. All 13 such publishers still have apps that haven't moved, and the switched app's first renewal will come round. |
| **CRM ↔ publisher link** | Cleaned website domain first. The name is used only when the website is missing or broken. No fuzzy matching. | A wrong match sends a rep to the wrong company, which is worse than a missed match. `crm_match_method` shows how each link was made. |
| **"Holdings" accounts** | Not linked, but shown as `possible_crm_*` on the publisher with the same name | They have their own domain, so the domain rule doesn't link them. But each one names exactly one publisher that has no CRM account ("Brightfin Holdings" ↔ "Brightfin"). Hiding that would let a rep cold-call a $408k customer. See below. |
| **Who is on the call list** | Any publisher with a switch or renewal signal. Publishers fully on Singular appear only when renewing. | A work list, not a directory (`golden_apps` is the directory). |
| **No CRM account / no owner** | Kept on the list (25 publishers with no CRM account, 27 rows with no owner) | Net-new leads. The rows with no owner are the routing queue for sales ops. |

**Which ARR column to use.** ARR belongs to the publisher, but the golden table has one row
per app, so the right column depends on the question:

| Question | Use | Example |
|---|---|---|
| Total ARR, or by territory / owner / account type | `SUM(publisher_active_arr)` | Total = $12.6M |
| ARR of publishers that have some app attribute (an MMP, a category, a platform) | One value per publisher, then sum: `select sum(arr) from (select publisher_id, max(publisher_arr_repeated) as arr from golden_apps where current_mmp = 'AppsFlyer' group by 1)` | Publishers using AppsFlyer = **$6.27M**. A plain `SUM(publisher_active_arr)` with that filter gives $4.73M, because it only counts publishers whose *top* app is on AppsFlyer. |
| Filter apps by account size | `publisher_arr_repeated` | Apps of publishers above $100k ARR |

`publisher_arr_repeated` must never be summed across apps: it repeats on every row.

**Ten publishers have a probable CRM account under a "Holdings" name.** Six are on today's call
list, and they must be checked before anyone calls:

| Rank | Publisher | Probable CRM account | Status | ARR | Owner |
|---|---|---|---|---|---|
| 11 | Ivory Studio | Ivory Studio Holdings | Prospect | – | Dara Nandakumar |
| 25 | Dune Mobile | Dune Mobile Holdings | Prospect | – | Anders Costa |
| 51 | Brightfin | Brightfin Holdings | **Customer** | **$408k** | Zara Okonkwo |
| 90 | Indigo Systems | Indigo Systems Holdings | **Customer** | **$38k** | Viktor Costa |
| 105 | Kiln Networks | Kiln Networks Holdings | Prospect | – | Dara Nandakumar |
| 110 | Lantern Group | Lantern Group Holdings | Partner | – | Dara Nandakumar |

The other four (Amber Apps, Larkspur Collective, Xenon Play, and **Ridge Company, a $657k
Customer**) have no signal today, but carry the same columns in `golden_apps`. The rule is
narrow on purpose: the names must match exactly once "Holdings" is removed, and neither side
may already be linked. A human then confirms each pair in the CRM.

### Being on the Singular SDK is not the same as being a Singular customer

I first assumed that an app on the Singular SDK meant a Singular customer. The data says
otherwise:

- **39 of the 42 CRM Customers have no app on the Singular SDK.** They hold $11.7M of the
  $12.6M ARR.
- **21 Prospects already run the Singular SDK** on at least one app, 5 of them on every app.
- **All 3 publishers whose app recently left the Singular SDK are Prospects** in the CRM.

Customers may buy products that need no SDK, or the CRM may be out of date. The data can't
tell which, so this went back to the team as a question. Meanwhile the call list keeps the
two facts in **separate columns**, so neither is inferred from the other:

| `signal`: what happened (SDK) | Today |
|---|---|
| `left_singular_sdk`: an app replaced the Singular SDK | 3 |
| `competitor_renewal`: a competitor contract renews within 90 days | 96 |
| `switched_to_competitor`: moved between competitors | 13 |
| `adopted_singular_sdk`: moved an app to Singular, other apps still elsewhere | 5 |
| `singular_sdk_renewal`: only Singular SDK apps are renewing | 3 |

| `sales_motion`: who we are to them (CRM) | Who acts | Today |
|---|---|---|
| `existing_customer` | The account owner. Never a cold call. | 13 |
| `win_back` | The account owner | 12 |
| `partner` | Partnerships | 7 |
| `verify_crm_account` | Sales ops: confirm the probable "Holdings" account first | 6 |
| `new_business` | Sales (sales ops routes the rows with no owner) | 82 |

The combination is what a rep needs. For example, 12 **existing customers ($3.8M ARR) have a
competitor attribution contract renewing**. That is a cross-sell to someone who already pays
us, not new business. `crm_sdk_mismatch` flags every publisher where the CRM and the SDK
disagree, for RevOps to resolve.

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
  a timer. The rebuild runs `main.py --as-of <run date>`, so renewal windows move with the
  calendar instead of staying at 2026-09-14, and it only signals the sync once the data tests
  pass. The sync pushes one record per CRM account, for *all* linked accounts, not just
  those on the call list.
- **No duplicates on retry.** The sync only **updates** existing accounts by their CRM id, so
  a retry rewrites the same values. A small state table keeps a fingerprint of what was last
  sent to each account, and only changed accounts are pushed. That also catches changes
  caused by time passing, such as a renewal window opening.
- **Why it never creates accounts.** The 25 call-list publishers with no linked account never
  reach the CRM through the sync. That is deliberate. Creating them would have made a second
  "Brightfin" next to "Brightfin Holdings", a $408k customer. They reach reps through the
  call list instead (`verify_crm_account` / `new_business`). Once sales ops creates or fixes
  the account, the next rebuild links it and the sync picks it up.
- **No stale fields.** Fields are cleared when a publisher leaves the call list, and also when
  an account loses its link altogether (for example, its website is edited). The sync blanks
  every account it wrote to before that is no longer in the payload.
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
- **Running these tests before the CRM sync**, so bad data never reaches reps:
  `WAREHOUSE_PATH=<new build> uv run pytest -m "not fixture_data"` tests the build that was
  just made. Tests that pin known cases of the sample data are marked `fixture_data` and skipped.
- **Comparing each run with the previous one**: row counts, the CRM match rate (76% today)
  and total ARR, with an alert on a large swing.
- **Checking that the sources agree with each other**, not just with themselves. Every source
  can be internally valid while the CRM and the product data drift apart. `crm_sdk_mismatch`
  turns this into a tracked number: today 39 Customers have no Singular SDK, 21 Prospects and
  4 Churned accounts run it. A jump means one side changed.
- **Freshness alerts** if a source stops updating.

## Assumptions

- **We are Singular,** but an app on the Singular SDK is *not* assumed to be a customer (see
  above). The commercial relationship comes from the CRM only.
- **No uninstall date exists**, so the latest install is the current MMP.
- **Renewal dates are tracked per app, from the install date.** The brief says contracts renew
  "unless the *publisher* moves", which suggests one contract per publisher. The data agrees:
  when a publisher runs one MMP on several apps (179 cases), the installs all fall within 45
  days of each other (median 18), like one contract rolled out app by app. Using each app's
  own date, and the earliest one per publisher, puts the renewal at most 45 days off a
  per-contract date, and 83 of the 96 competitor renewals already come from the main MMP.
- **Performance figures cover the same period** for every app, so they can be added together.
- **Duplicate CRM accounts are the same company**, so ARR is not added up.

### Questions asked, and the team's answers

| Question | Answer | Effect |
|---|---|---|
| Is 90 days the right renewal window? | Yes | None |
| Is 180 days right for "recently switched"? | Use 120 days | Call list went from 138 to 120 publishers |
| Which ranking metric does sales prefer? | Judgement call | Judgement call #1 |
| How should duplicate CRM accounts be resolved? | Judgement call | Judgement call #2 |
| Are "Holdings" accounts parents of publishers? | Use domains as the key | Not linked, but shown as a probable account to verify (10 publishers) |
| Can an app run two MMPs at once? | Part of the main-MMP rule | Judgement call #3 |

**Still open:** why do 39 of 42 paying Customers run no Singular SDK, while 21 Prospects do?
Until the team answers, the call list takes the relationship from the CRM only, and
`crm_sdk_mismatch` marks every case.

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