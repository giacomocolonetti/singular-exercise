"""Push product-side facts into the CRM whenever the marts are rebuilt.

Skeleton for Airflow 3 (not run here: Airflow is not a project dependency). Design
rationale is in the README, section "Airflow: CRM sync".

Flow:  ingestion DAGs ──(assets)──▶ build_marts DAG ──(assets)──▶ this DAG ──▶ CRM

    build_payload ─▶ diff_against_sync_state ─▶ chunk ─▶ push_batch (mapped, one per batch)

Guarantees:
  * Grain: one CRM account (crm_id) per record. Only accounts already matched to a
    publisher are touched. The sync never creates CRM accounts, so it cannot create
    duplicates; unmatched publishers go to sales ops through the marts instead.
  * Idempotent retries: each record is an update keyed on crm_id, so sending it twice
    writes the same values twice. sync_state stores the hash of the last payload pushed
    per account, so a re-run only sends what actually changed.
  * Halfway failure: batches are mapped tasks. Each batch records its own sync_state rows
    when it succeeds, and a retry re-runs only the failed batches.
  * Manual edits: we only write Product_* fields, which are read-only for reps, so a
    rep's own edits are never in our payload. See FIELD_MAP.
"""

import hashlib
import json
from datetime import timedelta

from airflow.sdk import Asset, dag, task

# Produced by the build_marts DAG (outlets=[...]), which is itself scheduled on the ingestion
# DAGs' assets, so a change in any product-side table flows all the way here.
GOLDEN_APPS = Asset("s3://lake/marts/golden_apps")
PUBLISHER_OPPORTUNITIES = Asset("s3://lake/marts/publisher_opportunities")

BATCH_SIZE = 200  # Salesforce Composite API accepts up to 200 records per call

# Every field we write is owned by the integration: created for this sync and read-only for
# reps (field-level security). Rep-owned fields (owner, stage, notes, ...) are never in the payload.
FIELD_MAP = {
    "opportunity_type": "Product_Opportunity_Type__c",
    "priority_rank": "Product_Priority_Rank__c",
    "current_mmps": "Product_Current_MMPs__c",
    "main_mmp": "Product_Main_MMP__c",
    "next_renewal_date": "Product_Next_Renewal_Date__c",
    "last_switch_date": "Product_Last_MMP_Switch_Date__c",
    "app_count": "Product_App_Count__c",
    "singular_app_count": "Product_Singular_App_Count__c",
}

# One record per matched CRM account: all of them, not only those on the call list, so
# that when a publisher leaves the list its opportunity fields get cleared in the CRM.
PAYLOAD_SQL = """
    select
        g.crm_id,
        o.opportunity_type,
        o.rank                                                          as priority_rank,
        string_agg(distinct g.current_mmp, ', ' order by g.current_mmp) as current_mmps,
        o.main_mmp,
        min(g.next_renewal_date)                                        as next_renewal_date,
        max(g.switch_date)                                              as last_switch_date,
        count(*)                                                        as app_count,
        count_if(g.is_on_singular)                                      as singular_app_count
    from golden_apps as g
    left join publisher_opportunities as o using (publisher_id)
    where g.crm_id is not null
    group by g.crm_id, o.opportunity_type, o.rank, o.main_mmp
"""


def notify_engineering(context) -> None:
    """Runs once the retry has failed too: page the data team with a link to the logs."""
    ti = context["task_instance"]
    # e.g. SlackWebhookHook(slack_webhook_conn_id="slack_data_alerts").send(text=...)
    print(f"[ALERT] {ti.dag_id}.{ti.task_id} failed after retries: {ti.log_url}")


def payload_hash(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()


@dag(
    schedule=[GOLDEN_APPS, PUBLISHER_OPPORTUNITIES],  # runs when both marts have been rebuilt
    catchup=False,
    max_active_runs=1,  # two runs racing on sync_state could push stale values last
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        "retry_exponential_backoff": True,
        "on_failure_callback": notify_engineering,
    },
    tags=["crm", "reverse-etl"],
)
def crm_sync():

    @task
    def build_payload() -> list[dict]:
        # warehouse = DuckDB / Trino / Athena hook over the lake
        rows = warehouse.execute(PAYLOAD_SQL)  # noqa: F821 (placeholder connection)
        return [{**row, "payload_hash": payload_hash(row)} for row in rows]

    @task
    def diff_against_sync_state(records: list[dict]) -> list[dict]:
        """Keep only accounts whose payload changed since the last successful push."""
        last_pushed = dict(warehouse.execute(  # noqa: F821
            "select crm_id, payload_hash from ops.crm_sync_state"
        ))
        return [r for r in records if last_pushed.get(r["crm_id"]) != r["payload_hash"]]

    @task
    def chunk(records: list[dict]) -> list[list[dict]]:
        return [records[i:i + BATCH_SIZE] for i in range(0, len(records), BATCH_SIZE)]

    @task(max_active_tis_per_dag=2)  # stay well inside the CRM API rate limit
    def push_batch(batch: list[dict]) -> None:
        updates = [
            {"Id": r["crm_id"], **{FIELD_MAP[k]: r[k] for k in FIELD_MAP}}
            for r in batch
        ]
        # PATCH by Id: an update of an existing record, never an insert.
        results = crm.update_records("Account", updates, all_or_none=True)  # noqa: F821
        if not all(res.success for res in results):
            raise RuntimeError(f"CRM rejected part of the batch: {results}")
        # Only after the CRM accepted the batch: remember what we pushed.
        warehouse.executemany(  # noqa: F821
            """
            insert or replace into ops.crm_sync_state (crm_id, payload_hash, pushed_at)
            values (?, ?, now())
            """,
            [(r["crm_id"], r["payload_hash"]) for r in batch],
        )

    push_batch.expand(batch=chunk(diff_against_sync_state(build_payload())))


crm_sync()
