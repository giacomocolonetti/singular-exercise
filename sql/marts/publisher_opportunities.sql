-- Grain: one row per publisher that has a live signal: a recent MMP switch, or a renewal
-- inside the window. Publishers entirely on the Singular SDK appear only when renewing.
-- Ranked top-down by performance so a rep can work the list in order.
--
-- Two separate questions, two columns:
--   signal       = what happened in the product data (SDK facts only)
--   sales_motion = who we are to this company, from the CRM status
-- They are kept apart because the data shows that being on the Singular SDK and being a
-- Singular customer are different things: 39 of 42 CRM Customers use no Singular SDK.
--
-- Built from golden_apps, so every number here reconciles with the golden table. The main
-- MMP rule lives in int_publisher_mmp, shared with golden_apps.
with latest_switch as (
    select
        publisher_id,
        max(switch_date)                                        as last_switch_date,
        arg_max(previous_mmp, (switch_date, downloads))         as switched_from,
        arg_max(current_mmp,  (switch_date, downloads))         as switched_to
    from golden_apps
    where is_recent_switch
    group by publisher_id
),

rollup as (
    select
        publisher_id,
        any_value(publisher_name)               as publisher_name,
        any_value(publisher_domain)             as publisher_domain,
        -- CRM attributes are per publisher already: identical on every app row
        any_value(crm_id)                       as crm_id,
        any_value(crm_match_method)             as crm_match_method,
        any_value(account_type)                 as account_type,
        any_value(account_owner)                as account_owner,
        any_value(territory)                    as territory,
        any_value(possible_crm_id)              as possible_crm_id,
        any_value(crm_sdk_mismatch)             as crm_sdk_mismatch,
        sum(publisher_active_arr)               as publisher_active_arr,

        count(*)                                as app_count,
        sum(downloads)                          as downloads,
        sum(users)                              as users,
        sum(revenue)                            as revenue,
        count_if(is_on_singular)                as singular_app_count,

        -- switch signals
        count_if(is_recent_switch)                                              as apps_switched_recently,
        count_if(is_recent_switch and previous_mmp = 'Singular')                as apps_left_singular_sdk,
        count_if(is_recent_switch and current_mmp  = 'Singular')                as apps_adopted_singular_sdk,
        count_if(is_recent_switch and current_mmp <> 'Singular')                as apps_switched_to_competitor,

        -- renewal signals
        min(next_renewal_date)                                                  as next_renewal_date,
        arg_min(current_mmp, (next_renewal_date, -downloads))                   as next_renewal_mmp,
        count_if(is_in_renewal_window)                                          as apps_renewing_in_window,
        count_if(is_in_renewal_window and not is_on_singular)                   as competitor_apps_renewing,
        count_if(is_in_renewal_window and is_on_singular)                       as singular_apps_renewing
    from golden_apps
    group by publisher_id
),

signals as (
    select
        r.*,
        m.main_mmp,
        m.mmp_mix,
        m.current_mmp_count,
        s.last_switch_date,
        s.switched_from,
        s.switched_to,
        r.apps_switched_recently > 0     as is_recent_switcher,
        r.apps_renewing_in_window > 0    as is_approaching_renewal,
        -- What happened (SDK facts), one per publisher, most urgent first. A publisher fully on
        -- the Singular SDK can only get 'singular_sdk_renewal': nothing left to win there.
        case
            when r.apps_left_singular_sdk > 0       then 'left_singular_sdk'       -- an app dropped our SDK
            when r.competitor_apps_renewing > 0     then 'competitor_renewal'      -- competitor contract up soon
            when r.apps_switched_to_competitor > 0  then 'switched_to_competitor'  -- moved between competitors
            when r.apps_adopted_singular_sdk > 0
             and r.singular_app_count < r.app_count then 'adopted_singular_sdk'    -- other apps still elsewhere
            when r.singular_apps_renewing > 0       then 'singular_sdk_renewal'
        end as signal,
        -- Who we are to them (CRM status), which decides who acts.
        case
            when r.account_type = 'Customer'        then 'existing_customer'   -- account owner; never a cold call
            when r.account_type = 'Churned'         then 'win_back'
            when r.account_type = 'Partner'         then 'partner'
            when r.crm_id is null
             and r.possible_crm_id is not null      then 'verify_crm_account'  -- check the probable account first
            else 'new_business'                                                -- Prospect, or no CRM account
        end as sales_motion
    from rollup as r
    join int_publisher_mmp as m using (publisher_id)
    left join latest_switch as s using (publisher_id)
),

main_mmp_renewal as (
    select g.publisher_id, min(g.next_renewal_date) as main_mmp_next_renewal_date
    from golden_apps as g
    join int_publisher_mmp as m on m.publisher_id = g.publisher_id and m.main_mmp = g.current_mmp
    group by g.publisher_id
),

scored as (
    select
        *,
        -- percentile ranks put the three metrics on the same 0-1 scale, so no single one dominates
        round((percent_rank() over (order by downloads)
             + percent_rank() over (order by users)
             + percent_rank() over (order by revenue)) / 3, 4)  as performance_score,
        rank() over (order by downloads desc)                    as downloads_rank,
        rank() over (order by users desc)                        as users_rank,
        rank() over (order by revenue desc)                      as revenue_rank
    from signals
    where signal is not null
)

select
    row_number() over (order by s.performance_score desc, s.revenue desc, s.publisher_id) as rank,
    s.signal,
    s.sales_motion,
    s.crm_sdk_mismatch,

    -- who
    s.publisher_id,
    s.publisher_name,
    s.publisher_domain,
    s.account_owner,
    s.account_type,
    s.territory,
    s.crm_id,
    s.crm_match_method,
    s.publisher_active_arr,
    -- probable CRM account not linked by domain: verify before calling (see int_publisher_crm)
    pc.possible_crm_account_name,
    pc.possible_account_type,
    pc.possible_account_owner,
    pc.possible_active_arr,

    -- how big
    s.performance_score,
    s.downloads,
    s.users,
    s.revenue,
    s.downloads_rank,
    s.users_rank,
    s.revenue_rank,
    s.app_count,

    -- which MMP
    s.main_mmp,
    s.mmp_mix,
    s.current_mmp_count,
    s.singular_app_count,

    -- switch signal
    s.is_recent_switcher,
    s.last_switch_date,
    s.switched_from,
    s.switched_to,
    s.apps_switched_recently,

    -- renewal signal
    s.is_approaching_renewal,
    s.next_renewal_date,
    date_diff('day', getvariable('as_of_date'), s.next_renewal_date)                  as days_to_renewal,
    cast(s.next_renewal_date - to_days(getvariable('renewal_window_days')) as date)   as renewal_window_opens,
    s.next_renewal_mmp,
    r.main_mmp_next_renewal_date,
    s.apps_renewing_in_window,

    getvariable('as_of_date') as as_of_date
from scored as s
join main_mmp_renewal as r using (publisher_id)
join int_publisher_crm as pc using (publisher_id)
order by rank
