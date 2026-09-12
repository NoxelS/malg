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
from malg.database.jobs import (
    cancel_job,
    claim_next_job,
    complete_job,
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
)

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
    "cancel_job",
    "claim_next_job",
    "complete_job",
    "enqueue_job",
    "fail_job",
    "persist_account_candidate",
    "persist_campaign",
    "persist_icp",
    "persist_icps",
    "renew_claim",
]
