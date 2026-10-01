"""golden_apps: one row per app, and every number adds back to the source."""


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
           or next_renewal_date <= current_mmp_install_date
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

def test_known_switch(con):
    row = con.sql("""
        select previous_mmp, current_mmp, switch_date::varchar, is_recent_switch
        from golden_apps where app_id = 'APP-1001'
    """).fetchone()
    assert row == ("AppsFlyer", "Adjust", "2026-07-02", True)


def test_duplicate_crm_accounts_keep_arr_and_owner(con):
    # Two Petrel Studios records: one holds the $179k ARR with no owner, the other the owner.
    row = con.sql("""
        select account_owner, active_arr, crm_duplicate_count
        from int_publisher_crm where crm_account_name like 'Petrel Studios%'
    """).fetchone()
    assert row == ("Viktor Costa", 179000, 1)


def test_record_flagged_as_duplicate_never_wins(scalar):
    assert scalar("select count(*) from int_publisher_crm where crm_account_name ilike '%(duplicate)%'") == 0