"""PostgreSQL persistence schema for campaign and ICP artifacts.

The database package defines storage only. Callers own engine construction,
schema creation, and all reads and writes; agents do not access this package.
"""

from malg.database.artifacts import persist_icps
from malg.database.models import ICP, Base, Campaign

__all__ = ["ICP", "Base", "Campaign", "persist_icps"]
