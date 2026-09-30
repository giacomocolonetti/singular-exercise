-- Normalisation rules shared by the product and CRM sides, so both sides of a join
-- are cleaned by exactly the same code.

-- 'HTTPS://www.Foo.com/?utm_source=crm ' -> 'foo.com'.
-- Placeholders such as 'N/A', 'n/', 'none' carry no dot, so they become NULL rather than
-- a bogus key that would match other junk.
create or replace macro normalize_domain(url) as (
    with cleaned as (
        select regexp_replace(
                   regexp_replace(
                       regexp_replace(lower(trim(url)), '^[a-z]+://', ''),
                       '^www\.', ''),
                   '[/?#].*$', '') as domain
    )
    select case when contains(domain, '.') then domain end from cleaned
);

-- 'Tundra Solutions Inc' / 'Jetty Ventures (duplicate)' -> 'tundra solutions' / 'jetty ventures'.
-- Only legal-form suffixes are stripped. 'Holdings' is deliberately kept: 'Brightfin Holdings'
-- has its own domain and may be a different legal entity from 'Brightfin'.
create or replace macro normalize_company_name(name) as (
    trim(regexp_replace(
        trim(regexp_replace(
            regexp_replace(lower(name), '\(duplicate\)', '', 'g'),
            '[^a-z0-9]+', ' ', 'g')),
        ' (inc|llc|ltd|gmbh|corp)$', ''))
);