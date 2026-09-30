-- Grain: one row per app.
-- Turns the install history into: which MMP the app is on today, whether it switched,
-- and when its current contract renews.
--
-- Rules (assumptions, see README):
--   * No uninstall date exists, so the most recent install is the current MMP and every
--     earlier one is treated as replaced.
--   * A switch needs at least two installs; an app's first-ever install is not a switch.
--   * Contracts run 12 months from the install date and auto-renew yearly.
with installs as (
    select
        *,
        row_number() over (partition by app_id order by install_date desc, mmp) as recency_rank,
        count(*) over (partition by app_id)                                     as mmp_count
    from stg_sdk_installs
    where install_date <= getvariable('as_of_date')  -- as-of semantics: ignore future-dated rows
),

history as (
    select
        app_id,
        string_agg(mmp || ' ' || strftime(install_date, '%Y-%m-%d'), ' → ' order by install_date) as mmp_history
    from installs
    group by app_id
),

per_app as (
    select
        cur.app_id,
        cur.mmp                                   as current_mmp,
        cur.install_date                          as current_mmp_install_date,
        cur.mmp = 'Singular'                      as is_on_singular,
        prev.mmp                                  as previous_mmp,
        prev.install_date                         as previous_mmp_install_date,
        cur.mmp_count,
        cur.mmp_count > 1                         as has_switched_mmp,
        case when cur.mmp_count > 1 then cur.install_date end as switch_date,
        -- complete contract years elapsed since the current install
        date_sub('year', cur.install_date, getvariable('as_of_date')) as contract_years_elapsed
    from installs as cur
    left join installs as prev
        on prev.app_id = cur.app_id
       and prev.recency_rank = 2
    where cur.recency_rank = 1
),

with_renewal as (
    select
        *,
        -- next anniversary on/after today, never the install day itself
        -- (29 Feb installs renew on 28 Feb in non-leap years)
        cast(case
            when contract_years_elapsed >= 1
             and current_mmp_install_date + to_years(contract_years_elapsed) >= getvariable('as_of_date')
                then current_mmp_install_date + to_years(contract_years_elapsed)
            else current_mmp_install_date + to_years(contract_years_elapsed + 1)
        end as date) as next_renewal_date
    from per_app
)

select
    r.app_id,
    r.current_mmp,
    r.current_mmp_install_date,
    r.is_on_singular,
    r.previous_mmp,
    r.previous_mmp_install_date,
    r.mmp_count,
    h.mmp_history,
    r.has_switched_mmp,
    r.switch_date,
    date_diff('day', r.switch_date, getvariable('as_of_date'))                    as days_since_switch,
    coalesce(date_diff('day', r.switch_date, getvariable('as_of_date'))
             <= getvariable('recent_switch_days'), false)                        as is_recent_switch,
    r.next_renewal_date,
    date_diff('day', getvariable('as_of_date'), r.next_renewal_date)              as days_to_renewal,
    date_diff('day', getvariable('as_of_date'), r.next_renewal_date)
        <= getvariable('renewal_window_days')                                     as is_in_renewal_window
from with_renewal as r
join history as h using (app_id)