-- Grain: one row per app (app_id is unique and every app has a publisher).
select
    trim(app_id)                                  as app_id,
    trim(publisher_id)                            as publisher_id,
    trim(app_name)                                as app_name,
    trim(publisher_name)                          as publisher_name,
    lower(trim(app_domain))                       as app_domain,
    normalize_domain(publisher_domain)            as publisher_domain,
    normalize_company_name(publisher_name)        as publisher_name_key
from read_csv('app_identification.csv', all_varchar = true)