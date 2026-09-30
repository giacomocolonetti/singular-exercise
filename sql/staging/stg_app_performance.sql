-- Grain: one row per app x system (iOS / Android). Some apps ship on one platform only.
select
    trim(app_id)                          as app_id,
    trim(system)                          as system,
    cast(downloads as bigint)             as downloads,
    cast(users as bigint)                 as users,
    cast(revenue as decimal(18, 2))       as revenue
from read_csv('app_performance.csv', all_varchar = true)