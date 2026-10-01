-- Grain: one row per publisher. Which MMP the publisher is "on", defined once and used by
-- both golden_apps and publisher_opportunities.
--
-- A publisher can run several MMPs at once. Main MMP = the current MMP handling the largest
-- share of the publisher's downloads (an MMP attributes installs, so download volume is what
-- it handles and bills on), then the one on the most apps, then the most recently adopted.
with app_downloads as (
    select app_id, sum(downloads) as downloads
    from stg_app_performance
    group by app_id
),

per_mmp as (
    select
        app.publisher_id,
        mmp.current_mmp,
        coalesce(sum(d.downloads), 0)           as downloads,
        count(*)                                as app_count,
        max(mmp.current_mmp_install_date)       as latest_install,
        round(100 * coalesce(sum(d.downloads), 0)
              / sum(sum(d.downloads)) over (partition by app.publisher_id)) as downloads_share_pct
    from stg_app_identification as app
    join int_app_mmp as mmp using (app_id)
    left join app_downloads as d using (app_id)
    group by app.publisher_id, mmp.current_mmp
)

select
    publisher_id,
    arg_max(current_mmp, (downloads, app_count, latest_install))               as main_mmp,
    -- e.g. 'Kochava 75% (1 app), Branch 25% (3 apps)': share of the publisher's downloads
    string_agg(
        current_mmp || ' ' || downloads_share_pct::int || '% ('
            || app_count || if(app_count = 1, ' app)', ' apps)'),
        ', ' order by downloads desc)                                          as mmp_mix,
    count(*)                                                                   as current_mmp_count
from per_mmp
group by publisher_id
