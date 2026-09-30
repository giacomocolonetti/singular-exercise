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
            having (bool_or(is_recent_switch) or bool_or(is_in_renewal_window))
               and not bool_and(is_on_singular)
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
        where not (is_recent_switcher or is_approaching_renewal) or opportunity_type is null
    """) == 0


def test_publishers_fully_on_singular_are_excluded(scalar):
    assert scalar("select count(*) from publisher_opportunities where singular_app_count = app_count") == 0


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


def test_churn_label_only_when_an_app_left_singular(scalar):
    assert scalar("""
        select count(*) from publisher_opportunities as p
        where opportunity_type = 'churned_from_singular'
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
