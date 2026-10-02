"""golden_apps: one row per app, and every number adds back to the source."""

import pytest


# --- grain -----------------------------------------------------------------------------

def test_one_row_per_app(scalar):
    assert scalar("select count(*) from golden_apps") == scalar(
        "select count(distinct app_id) from golden_apps"
    )


def test_no_app_lost_or_invented(scalar):
    assert scalar("select count(*) from golden_apps") == scalar(
        "select count(*) from stg_app_identification"
    )


# --- reconciliation: sums must match the source exactly --------------------------------

def test_performance_reconciles_with_source(con):
    golden = con.sql("select sum(downloads), sum(users), sum(revenue) from golden_apps").fetchone()
    source = con.sql("select sum(downloads), sum(users), sum(revenue) from stg_app_performance").fetchone()
    assert golden == source


def test_platform_columns_add_up_to_totals(scalar):
    assert scalar("""
        select count(*) from golden_apps
        where downloads <> coalesce(ios_downloads, 0) + coalesce(android_downloads, 0)
           or revenue   <> coalesce(ios_revenue, 0)   + coalesce(android_revenue, 0)
    """) == 0


def test_publisher_arr_is_counted_once(scalar):
    assert scalar("select sum(publisher_active_arr) from golden_apps") == scalar(
        "select sum(active_arr) from int_publisher_crm"
    )


def test_repeated_arr_is_the_publishers_arr_on_every_row(scalar):
    assert scalar("""
        select count(*) from (
            select publisher_id from golden_apps
            group by publisher_id
            having count(distinct publisher_arr_repeated) > 1
                or max(publisher_arr_repeated) is distinct from sum(publisher_active_arr)
        )
    """) == 0


@pytest.mark.fixture_data
def test_documented_query_gives_arr_under_an_app_filter(scalar):
    # README pattern for "ARR of publishers using AppsFlyer": one value per publisher, then sum.
    documented = scalar("""
        select sum(arr) from (
            select publisher_id, max(publisher_arr_repeated) as arr
            from golden_apps where current_mmp = 'AppsFlyer'
            group by publisher_id)
    """)
    truth = scalar("""
        select sum(active_arr) from int_publisher_crm
        where publisher_id in (select publisher_id from golden_apps where current_mmp = 'AppsFlyer')
    """)
    assert documented == truth == 6_271_000


def test_exactly_one_primary_row_per_publisher(scalar):
    assert scalar("""
        select count(*) from (
            select publisher_id from golden_apps
            group by publisher_id
            having count_if(is_publisher_primary_row) <> 1
        )
    """) == 0


def test_arr_only_on_primary_rows(scalar):
    assert scalar("""
        select count(*) from golden_apps
        where publisher_active_arr is not null and not is_publisher_primary_row
    """) == 0


# --- CRM matching ----------------------------------------------------------------------

def test_a_crm_account_belongs_to_at_most_one_publisher(scalar):
    assert scalar("""
        select count(*) from (
            select crm_id from int_publisher_crm
            where crm_id is not null
            group by crm_id having count(*) > 1
        )
    """) == 0


def test_possible_crm_account_only_when_no_confirmed_one(scalar):
    assert scalar("""
        select count(*) from int_publisher_crm
        where possible_crm_id is not null and crm_id is not null
    """) == 0


def test_possible_crm_account_is_not_used_elsewhere(scalar):
    # never the confirmed account of another publisher, never offered to two publishers
    assert scalar("""
        select count(*) from int_publisher_crm as p
        where p.possible_crm_id in (select crm_id from int_publisher_crm where crm_id is not null)
           or p.possible_crm_id in (
               select possible_crm_id from int_publisher_crm
               where possible_crm_id is not null
               group by possible_crm_id having count(*) > 1)
    """) == 0


def test_possible_crm_account_does_not_change_confirmed_arr(scalar):
    # possible accounts are surfaced for review, never counted as the publisher's ARR
    assert scalar("select sum(active_arr) from int_publisher_crm") == scalar("""
        select sum(active_arr) from stg_crm_accounts
        where crm_id in (select crm_id from int_publisher_crm)
    """)


def test_match_method_values(scalar):
    assert scalar("""
        select count(*) from golden_apps
        where crm_match_method not in ('domain', 'name', 'unmatched')
           or (crm_match_method = 'unmatched') <> (crm_id is null)
    """) == 0


# --- MMP and renewal rules -------------------------------------------------------------

def test_every_app_has_a_current_mmp_and_a_renewal_within_a_year(scalar):
    assert scalar("""
        select count(*) from golden_apps
        where current_mmp is null
           or next_renewal_date not between as_of_date and as_of_date + interval 1 year
           or next_renewal_date <= contract_start_date
           or contract_start_date > current_mmp_install_date
    """) == 0


def test_one_contract_per_publisher_and_mmp(scalar):
    # "auto-renew unless the publisher moves": apps of a publisher on the same MMP share one
    # contract, so a later sibling app can never create a renewal of its own.
    assert scalar("""
        select count(*) from (
            select publisher_id, current_mmp from golden_apps
            group by all
            having count(distinct contract_start_date) > 1 or count(distinct next_renewal_date) > 1
                or min(contract_start_date) > min(current_mmp_install_date)
        )
    """) == 0


def test_contract_starts_where_the_unbroken_period_on_the_mmp_starts(scalar):
    # Recomputed from the raw installs: no app of the publisher was already on that MMP when
    # the contract supposedly started. If one was, the contract started earlier (an app that
    # later moved off keeps the contract running while a sibling stays).
    assert scalar("""
        with spans as (
            select a.publisher_id, i.mmp, i.install_date as span_start,
                   lead(i.install_date) over (partition by i.app_id order by i.install_date) as span_end
            from stg_sdk_installs as i join stg_app_identification as a using (app_id)
        ),
        contracts as (
            select distinct publisher_id, current_mmp as mmp, contract_start_date from golden_apps
        )
        select count(*) from contracts as c
        join spans as s using (publisher_id, mmp)
        where s.span_start < c.contract_start_date
          and coalesce(s.span_end, date '9999-12-31') >= c.contract_start_date
    """) == 0


def test_renewal_window_flag_matches_threshold(scalar):
    assert scalar("""
        select count(*) from golden_apps
        where is_in_renewal_window <> (days_to_renewal <= getvariable('renewal_window_days'))
    """) == 0


def test_single_install_is_never_a_switch(scalar):
    assert scalar("select count(*) from golden_apps where mmp_count = 1 and has_switched_mmp") == 0


def test_every_publisher_has_one_main_mmp_it_actually_uses(scalar):
    assert scalar("""
        select count(*) from (
            select publisher_id from golden_apps
            group by publisher_id
            having count(distinct publisher_main_mmp) <> 1
                or not bool_or(current_mmp = publisher_main_mmp)
        )
    """) == 0


def test_main_mmp_agrees_across_tables(scalar):
    assert scalar("""
        select count(*) from publisher_opportunities as p
        join (select distinct publisher_id, publisher_main_mmp from golden_apps) as g using (publisher_id)
        where p.main_mmp <> g.publisher_main_mmp
    """) == 0


# --- known cases, checked by hand against the CSVs -------------------------------------

@pytest.mark.fixture_data
def test_known_switch(con):
    row = con.sql("""
        select previous_mmp, current_mmp, switch_date::varchar, is_recent_switch
        from golden_apps where app_id = 'APP-1001'
    """).fetchone()
    assert row == ("AppsFlyer", "Adjust", "2026-07-02", True)


@pytest.mark.fixture_data
def test_contract_that_just_renewed_is_not_in_the_window(con):
    # Jubilee Interactive's AppsFlyer contract started 2025-08-28 and renewed on 2026-08-28.
    # Jubilee Charge joined it on 2025-09-24: that must not look like a renewal on 2026-09-24.
    rows = con.sql("""
        select app_name, contract_start_date::varchar, next_renewal_date::varchar, is_in_renewal_window
        from golden_apps where publisher_name = 'Jubilee Interactive' and current_mmp = 'AppsFlyer'
        order by app_name
    """).fetchall()
    assert rows == [
        ("Jubilee Charge", "2025-08-28", "2027-08-28", False),
        ("Jubilee Kids", "2025-08-28", "2027-08-28", False),
    ]


@pytest.mark.fixture_data
def test_contract_started_by_an_app_that_later_moved_off(con):
    # Ivory Saga started Ivory Digital's Adjust contract on 2022-08-29, then moved to Branch
    # in 2026-07. Ivory Table and Ivory Pay (Adjust since 2022-10-07) kept the contract going,
    # so it renewed on 2026-08-29, not on 2026-10-07.
    rows = con.sql("""
        select app_name, contract_start_date::varchar, next_renewal_date::varchar, is_in_renewal_window
        from golden_apps where publisher_name = 'Ivory Digital' and current_mmp = 'Adjust'
        order by app_name
    """).fetchall()
    assert rows == [
        ("Ivory Pay", "2022-08-29", "2027-08-29", False),
        ("Ivory Table", "2022-08-29", "2027-08-29", False),
    ]


@pytest.mark.fixture_data
def test_duplicate_crm_accounts_keep_arr_and_owner(con):
    # Two Petrel Studios records: one holds the $179k ARR with no owner, the other the owner.
    row = con.sql("""
        select account_owner, active_arr, crm_duplicate_count
        from int_publisher_crm where crm_account_name like 'Petrel Studios%'
    """).fetchone()
    assert row == ("Viktor Costa", 179000, 1)


@pytest.mark.fixture_data
def test_holdings_account_is_surfaced_not_merged(con):
    # 'Brightfin Holdings' ($408k Customer) has its own domain: not linked, but shown to the rep.
    row = con.sql("""
        select crm_match_method, crm_id, possible_crm_account_name, possible_account_type, possible_active_arr
        from int_publisher_crm where publisher_id = 'PUB-0009'
    """).fetchone()
    assert row == ("unmatched", None, "Brightfin Holdings", "Customer", 408000)


def test_record_flagged_as_duplicate_never_wins(scalar):
    assert scalar("select count(*) from int_publisher_crm where crm_account_name ilike '%(duplicate)%'") == 0