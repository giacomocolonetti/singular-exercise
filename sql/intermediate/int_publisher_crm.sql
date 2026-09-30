-- Grain: one row per publisher (all of them), carrying the CRM view of that company.
--
-- 1. Match each CRM account to a publisher: normalised domain first, then normalised
--    name as a fallback for accounts whose website is missing or mistyped. No fuzzy
--    matching: pointing a rep at the wrong account costs more than a missed match.
-- 2. Duplicates = several CRM accounts resolving to the same publisher. They are merged
--    field by field (see README), and the extra ids are listed so RevOps can clean them up.
with publishers as (
    select distinct publisher_id, publisher_domain, publisher_name_key
    from stg_app_identification
),

domain_matches as (
    select crm.*, pub.publisher_id, 'domain' as match_method
    from stg_crm_accounts as crm
    join publishers as pub on crm.company_domain = pub.publisher_domain
),

name_matches as (
    select crm.*, pub.publisher_id, 'name' as match_method
    from stg_crm_accounts as crm
    join publishers as pub on crm.account_name_key = pub.publisher_name_key
    where crm.crm_id not in (select crm_id from domain_matches)  -- a domain match always wins
),

matches as (
    select
        *,
        case account_type
            when 'Customer' then 4
            when 'Partner'  then 3
            when 'Churned'  then 2  -- a past customer is a stronger relationship than a prospect
            when 'Prospect' then 1
            else 0
        end as type_rank
    from (select * from domain_matches union all by name select * from name_matches)
),

ranked as (
    select
        *,
        row_number() over (
            partition by publisher_id
            order by
                account_name ilike '%(duplicate)%',  -- a record a human already flagged never wins
                type_rank desc, active_arr desc, last_activity_date desc, crm_id
        ) as record_rank
    from matches
),

merged as (
    select
        publisher_id,
        any_value(crm_id)             filter (where record_rank = 1)  as crm_id,
        any_value(account_name)       filter (where record_rank = 1)  as crm_account_name,
        any_value(account_type)       filter (where record_rank = 1)  as account_type,
        any_value(territory)          filter (where record_rank = 1)  as territory,
        -- the owner comes from the most recently worked record that has one
        arg_max(account_owner, last_activity_date) filter (where account_owner is not null) as account_owner,
        max(active_arr)                                                as active_arr,  -- same account twice: max, never sum
        max(last_activity_date)                                        as last_activity_date,
        case when bool_or(match_method = 'domain') then 'domain' else 'name' end as crm_match_method,
        count(*) - 1                                                   as crm_duplicate_count,
        string_agg(crm_id, ', ' order by crm_id) filter (where record_rank > 1) as crm_duplicate_ids
    from ranked
    group by publisher_id
)

select
    pub.publisher_id,
    m.crm_id,
    m.crm_account_name,
    coalesce(m.crm_match_method, 'unmatched')  as crm_match_method,
    coalesce(m.crm_duplicate_count, 0)         as crm_duplicate_count,
    m.crm_duplicate_ids,
    m.account_type,
    m.account_owner,
    m.territory,
    m.last_activity_date,
    m.active_arr
from (select distinct publisher_id from publishers) as pub
left join merged as m using (publisher_id)