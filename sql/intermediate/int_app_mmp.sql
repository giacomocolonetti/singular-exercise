-- Grain: one row per app.
-- Turns the install history into: which MMP the app is on today, whether it switched,
-- and when the contract covering it renews.
--
-- Rules (assumptions, see README):
--   * No uninstall date exists, so the most recent install is the current MMP and every
--     earlier one is treated as replaced.
--   * A switch needs at least two installs; an app's first-ever install is not a switch.
--   * The contract belongs to the publisher, not the app ("MMP contracts run for 12 months
--     and auto-renew unless the publisher moves"). A contract with an MMP lasts as long as
--     the publisher has at least one app on it without a break, and renews yearly from the
--     day that unbroken period began. An app added later joins it; an app that moves off
--     doesn't end it while a sibling stays. Only a full gap (no app on that MMP) ends it.
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
        app.publisher_id
    from installs as cur
    join stg_app_identification as app using (app_id)
    left join installs as prev
        on prev.app_id = cur.app_id
       and prev.recency_rank = 2
    where cur.recency_rank = 1
),

-- Each period an app spent on an MMP: from its install until the app's next install
-- (open-ended if it is still on it).
spans as (
    select
        i.app_id,
        a.publisher_id,
        i.mmp,
        i.install_date                                                                   as span_start,
        coalesce(lead(i.install_date) over (partition by i.app_id order by i.install_date),
                 date '9999-12-31')                                                      as span_end
    from installs as i
    join stg_app_identification as a using (app_id)
),

-- Group a publisher's periods on one MMP into unbroken stretches (one stretch = one contract):
-- a period opens a new contract only if every earlier period on that MMP had already ended.
contract_periods as (
    select
        *,
        sum(opens_contract) over (
            partition by publisher_id, mmp order by span_start, app_id
        ) as contract_no
    from (
        select
            *,
            case when span_start > coalesce(max(span_end) over (
                     partition by publisher_id, mmp order by span_start, app_id
                     rows between unbounded preceding and 1 preceding), date '0001-01-01')
                 then 1 else 0 end as opens_contract
        from spans
    )
),

current_contracts as (
    -- the contract each app is on today = the stretch containing its open-ended period
    select
        cur.app_id,
        min(p.span_start) as contract_start_date
    from contract_periods as cur
    join contract_periods as p
      on p.publisher_id = cur.publisher_id and p.mmp = cur.mmp and p.contract_no = cur.contract_no
    where cur.span_end = date '9999-12-31'
    group by cur.app_id
),

contracts as (
    select per_app.*, cc.contract_start_date
    from per_app
    join current_contracts as cc using (app_id)
),

with_renewal as (
    select
        *,
        -- next contract anniversary on/after today, never the start day itself
        -- (29 Feb starts renew on 28 Feb in non-leap years)
        cast(case
            when contract_years_elapsed >= 1
             and contract_start_date + to_years(contract_years_elapsed) >= getvariable('as_of_date')
                then contract_start_date + to_years(contract_years_elapsed)
            else contract_start_date + to_years(contract_years_elapsed + 1)
        end as date) as next_renewal_date
    from (
        select *, date_sub('year', contract_start_date, getvariable('as_of_date')) as contract_years_elapsed
        from contracts
    )
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
    r.contract_start_date,
    r.next_renewal_date,
    date_diff('day', getvariable('as_of_date'), r.next_renewal_date)              as days_to_renewal,
    date_diff('day', getvariable('as_of_date'), r.next_renewal_date)
        <= getvariable('renewal_window_days')                                     as is_in_renewal_window
from with_renewal as r
join history as h using (app_id)