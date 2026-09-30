-- Grain: one row per app x MMP install. An app can carry several MMPs over time.
-- `mmp_installed` is dropped on purpose: it is the global number of installs of that MMP
-- repeated on every row (e.g. 336 on every AppsFlyer row), so summing it would be wrong.
select
    trim(app_id)                     as app_id,
    trim(mmp)                        as mmp,
    cast(install_date as date)       as install_date
from read_csv('sdk_installs.csv', all_varchar = true)