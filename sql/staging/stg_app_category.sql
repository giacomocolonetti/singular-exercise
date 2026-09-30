-- Grain: one row per app.
select
    trim(app_id)            as app_id,
    trim(app_category)      as app_category
from read_csv('app_category.csv', all_varchar = true)