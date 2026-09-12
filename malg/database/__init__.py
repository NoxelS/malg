"""PostgreSQL persistence schema for campaign and ICP artifacts.

The database package defines storage only. Callers own engine construction,
schema creation, and all reads and writes; agents do not access this package.
"""

from malg.database.artifacts import persist_account_candidate, persist_icps
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
    "persist_account_candidate",
    "persist_icps",
]
