-- Grain: one row per CRM account. Not one row per company: a few companies have
-- duplicate accounts, which are resolved in int_publisher_crm.
select
    trim(crm_id)                                  as crm_id,
    trim(account_name)                            as account_name,
    website                                       as website_raw,
    normalize_domain(website)                     as company_domain,
    normalize_company_name(account_name)          as account_name_key,
    trim(type)                                    as account_type,
    cast(active_arr as bigint)                    as active_arr,
    cast(last_activity_date as date)              as last_activity_date,
    trim(territory)                               as territory,
    nullif(trim(account_owner), '')               as account_owner
from read_csv('crm_accounts.csv', all_varchar = true)