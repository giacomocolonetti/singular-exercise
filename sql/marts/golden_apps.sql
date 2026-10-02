-- Grain: one row per app (734 rows). The shared "what do we know about this app and its
-- publisher" table: identity, category, performance, MMP and the CRM view of its publisher.
--
-- Every numeric measure can be summed across any filter or group-by without double counting:
--   * performance is summed across platforms here (with per-platform columns kept for drill-down);
--   * MMP history is pivoted into current/previous columns instead of extra rows;
--   * publisher-level ARR is filled on exactly one row per publisher (is_publisher_primary_row).
with performance as (
    select
        app_id,
        string_agg(system, ', ' order by system)          as platforms,
        sum(downloads)                                      as downloads,
        sum(users)                                          as users,
        sum(revenue)                                        as revenue,
        sum(downloads) filter (where system = 'iOS')        as ios_downloads,
        sum(users)     filter (where system = 'iOS')        as ios_users,
        sum(revenue)   filter (where system = 'iOS')        as ios_revenue,
        sum(downloads) filter (where system = 'Android')    as android_downloads,
        sum(users)     filter (where system = 'Android')    as android_users,
        sum(revenue)   filter (where system = 'Android')    as android_revenue
    from stg_app_performance
    group by app_id
),

apps as (
    select
        app.*,
        cat.app_category,
        perf.* exclude (app_id),
        -- the publisher's top app by revenue carries the publisher-level ARR
        row_number() over (
            partition by app.publisher_id
            order by perf.revenue desc nulls last, app.app_id
        ) = 1 as is_publisher_primary_row
    from stg_app_identification as app
    left join stg_app_category as cat using (app_id)
    left join performance as perf using (app_id)
)

select
    getvariable('as_of_date')       as as_of_date,

    -- app
    a.app_id,
    a.app_name,
    a.app_domain,
    a.app_category,

    -- publisher
    a.publisher_id,
    a.publisher_name,
    a.publisher_domain,

    -- performance (additive)
    a.platforms,
    a.downloads,
    a.users,
    a.revenue,
    a.ios_downloads,
    a.ios_users,
    a.ios_revenue,
    a.android_downloads,
    a.android_users,
    a.android_revenue,

    -- MMP
    pm.main_mmp                     as publisher_main_mmp,  -- the publisher's main MMP (rule in int_publisher_mmp)
    m.current_mmp,
    m.current_mmp_install_date,
    m.is_on_singular,
    m.previous_mmp,
    m.previous_mmp_install_date,
    m.mmp_count,
    m.mmp_history,
    m.has_switched_mmp,
    m.switch_date,
    m.days_since_switch,
    m.is_recent_switch,
    m.next_renewal_date,
    m.days_to_renewal,
    m.is_in_renewal_window,

    -- CRM (publisher-level attributes repeat on each app; ARR does not, see header)
    c.crm_id,
    c.crm_account_name,
    c.crm_match_method,
    c.crm_duplicate_count,
    c.crm_duplicate_ids,
    c.account_type,
    c.account_owner,
    c.territory,
    c.last_activity_date,
    case when a.is_publisher_primary_row then c.active_arr end  as publisher_active_arr,
    a.is_publisher_primary_row,

    -- CRM status vs product usage disagree (publisher level): a data-quality signal for RevOps,
    -- not an error. A Customer may buy products that need no SDK, or the CRM may be stale.
    case
        when c.account_type = 'Customer' and not bool_or(m.is_on_singular) over w then 'customer_without_singular_sdk'
        when c.account_type = 'Prospect' and bool_or(m.is_on_singular) over w     then 'prospect_on_singular_sdk'
        when c.account_type = 'Churned'  and bool_or(m.is_on_singular) over w     then 'churned_on_singular_sdk'
    end as crm_sdk_mismatch,

    -- a CRM account that is probably this publisher but isn't linked by domain: verify before calling
    c.possible_crm_id,
    c.possible_crm_account_name,
    c.possible_account_type,
    c.possible_account_owner
from apps as a
left join int_app_mmp as m using (app_id)
left join int_publisher_crm as c using (publisher_id)
left join int_publisher_mmp as pm using (publisher_id)
window w as (partition by a.publisher_id)
order by a.publisher_id, a.app_id
