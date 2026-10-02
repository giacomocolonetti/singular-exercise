"""publisher_opportunities: one row per publisher, nobody missing, numbers agree with golden_apps."""


# --- grain -----------------------------------------------------------------------------

def test_a_publisher_never_appears_twice(scalar):
    assert scalar("select count(*) from publisher_opportunities") == scalar(
        "select count(distinct publisher_id) from publisher_opportunities"
    )


def test_no_eligible_publisher_is_missing(scalar):
    # Recomputed straight from golden_apps: catches a join or filter silently dropping publishers.
    assert scalar("""
        select count(*) from (
            select publisher_id from golden_apps
            group by publisher_id
            having bool_or(is_in_renewal_window)
                or (bool_or(is_recent_switch) and not bool_and(is_on_singular))
        ) as eligible
        anti join publisher_opportunities using (publisher_id)
    """) == 0


# --- reconciliation with golden_apps ---------------------------------------------------

def test_metrics_equal_the_sum_of_the_publishers_apps(scalar):
    assert scalar("""
        select count(*)
        from publisher_opportunities as p
        join (
            select publisher_id, count(*) as app_count, sum(downloads) as downloads,
                   sum(users) as users, sum(revenue) as revenue,
                   sum(publisher_active_arr) as arr
            from golden_apps group by publisher_id
        ) as g using (publisher_id)
        where p.app_count <> g.app_count or p.downloads <> g.downloads
           or p.users <> g.users or p.revenue <> g.revenue
           or p.publisher_active_arr is distinct from g.arr
    """) == 0


# --- business rules --------------------------------------------------------------------

def test_every_row_has_a_signal_and_a_type(scalar):
    assert scalar("""
        select count(*) from publisher_opportunities
        where not (is_recent_switcher or is_approaching_renewal)
           or signal is null or sales_motion is null
    """) == 0


def test_publishers_fully_on_singular_appear_only_as_renewals(scalar):
    assert scalar("""
        select count(*) from publisher_opportunities
        where singular_app_count = app_count
          and (signal <> 'singular_sdk_renewal' or not is_approaching_renewal)
    """) == 0


# --- sales motion comes from the CRM, never from the SDK -------------------------------

def test_a_paying_customer_is_never_treated_as_new_business(scalar):
    assert scalar("""
        select count(*) from publisher_opportunities
        where (account_type = 'Customer') <> (sales_motion = 'existing_customer')
    """) == 0


def test_motion_matches_crm_status(scalar):
    assert scalar("""
        select count(*) from publisher_opportunities
        where sales_motion <> case
            when account_type = 'Customer' then 'existing_customer'
            when account_type = 'Churned'  then 'win_back'
            when account_type = 'Partner'  then 'partner'
            when crm_id is null and possible_crm_account_name is not null then 'verify_crm_account'
            else 'new_business' end
    """) == 0


def test_probable_crm_account_is_always_verified_before_calling(scalar):
    # Brightfin (#50 on the list, $408k Customer under 'Brightfin Holdings') must not be cold-called.
    assert scalar("""
        select count(*) from publisher_opportunities
        where possible_crm_account_name is not null and sales_motion <> 'verify_crm_account'
    """) == 0


def test_crm_sdk_mismatch_follows_its_definition(scalar):
    assert scalar("""
        select count(*) from publisher_opportunities
        where crm_sdk_mismatch is distinct from case
            when account_type = 'Customer' and singular_app_count = 0 then 'customer_without_singular_sdk'
            when account_type = 'Prospect' and singular_app_count > 0 then 'prospect_on_singular_sdk'
            when account_type = 'Churned'  and singular_app_count > 0 then 'churned_on_singular_sdk'
        end
    """) == 0


def test_renewal_flag_matches_the_window(scalar):
    assert scalar("""
        select count(*) from publisher_opportunities
        where is_approaching_renewal <> (days_to_renewal <= getvariable('renewal_window_days'))
    """) == 0


def test_main_mmp_is_one_of_the_publishers_current_mmps(scalar):
    assert scalar("""
        select count(*) from publisher_opportunities as p
        where not exists (
            select 1 from golden_apps as g
            where g.publisher_id = p.publisher_id and g.current_mmp = p.main_mmp
        )
    """) == 0


def test_left_singular_sdk_only_when_an_app_left_singular(scalar):
    assert scalar("""
        select count(*) from publisher_opportunities as p
        where signal = 'left_singular_sdk'
          and not exists (
              select 1 from golden_apps as g
              where g.publisher_id = p.publisher_id and g.is_recent_switch and g.previous_mmp = 'Singular'
          )
    """) == 0


# --- ranking ---------------------------------------------------------------------------

def test_rank_is_contiguous_and_follows_the_score(con):
    ranks, scores = zip(*con.sql(
        "select rank, performance_score from publisher_opportunities order by rank"
    ).fetchall())
    assert list(ranks) == list(range(1, len(ranks) + 1))
    assert list(scores) == sorted(scores, reverse=True)
