"""PostgreSQL persistence schema for campaign and ICP artifacts.

The database package defines storage only. Callers own engine construction,
schema creation, and all reads and writes; agents do not access this package.
"""

from malg.database.artifacts import (
    persist_account_candidate,
    persist_campaign,
    persist_icp,
    persist_icps,
)
from malg.database.dashboard import get_dashboard_summary, list_active_workers
from malg.database.jobs import (
    cancel_job,
    cancel_running_jobs,
    claim_next_job,
    complete_job,
    enqueue_campaign_jobs,
    enqueue_job,
    fail_job,
    renew_claim,
)
from malg.database.models import (
    ICP,
    Account,
    AccountMatch,
    AccountValidationRun,
    Base,
    Campaign,
    CommunicationEndpoint,
    Contact,
    Employment,
    ResearchJob,
    WorkerHeartbeat,
)
from malg.database.workers import record_worker_heartbeat

__all__ = [
    "ICP",
    "Account",
    "AccountMatch",
    "AccountValidationRun",
    "Base",
    "Campaign",
    "CommunicationEndpoint",
    "Contact",
    "Employment",
    "ResearchJob",
    "WorkerHeartbeat",
    "cancel_job",
    "cancel_running_jobs",
    "claim_next_job",
    "complete_job",
    "enqueue_campaign_jobs",
    "enqueue_job",
    "fail_job",
    "get_dashboard_summary",
    "list_active_workers",
    "persist_account_candidate",
    "persist_campaign",
    "persist_icp",
    "persist_icps",
    "record_worker_heartbeat",
    "renew_claim",
]
