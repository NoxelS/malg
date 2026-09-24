"""Retire MALG-owned CRM business tables after Twenty cutover.

Revision ID: 20260923_02
Revises: 20260923_01

This migration is intentionally separate from additive contract fields. It must
only be deployed during the documented maintenance/backup cutover.
"""

from os import environ

from alembic import context, op

revision = "20260923_02"
down_revision = "20260923_01"
branch_labels = None
depends_on = None


_RETIRED_TABLES = (
    "lead_reviews",
    "leads",
    "account_validation_runs",
    "communication_endpoints",
    "employments",
    "contacts",
    "account_matches",
    "accounts",
    "icps",
    "campaigns",
    "artifact_versions",
)


def upgrade() -> None:
    """Drop local CRM tables only during an approved maintenance cutover."""
    if context.is_offline_mode():
        op.execute("-- REQUIRES MALG_CRM_CUTOVER_APPROVED=1, maintenance, and verified backups")
    elif environ.get("MALG_CRM_CUTOVER_APPROVED") != "1":
        raise RuntimeError(
            "Refusing local CRM retirement without MALG_CRM_CUTOVER_APPROVED=1 and verified backups."
        )
    for table in _RETIRED_TABLES:
        op.drop_table(table, if_exists=True)

def downgrade() -> None:
    """Restoration requires the archived database backup, not table recreation."""
    raise RuntimeError("CRM table restoration requires restoring the pre-cutover database backup")
