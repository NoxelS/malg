"""Add worker liveness and current claim timestamps.

Revision ID: 20260913_02
Revises: 20260913_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260913_02"
down_revision = "20260913_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist worker heartbeat timestamps and current claim start times."""
    op.add_column("research_jobs", sa.Column("claimed_at", sa.DateTime(timezone=True)))
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_token", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("worker_token"),
    )
    op.create_index("ix_worker_heartbeats_last_seen_at", "worker_heartbeats", ["last_seen_at"])


def downgrade() -> None:
    """Drop worker liveness and current claim timestamps."""
    op.drop_index("ix_worker_heartbeats_last_seen_at", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
    op.drop_column("research_jobs", "claimed_at")
