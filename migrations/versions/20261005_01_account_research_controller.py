"""Persist continuous account-research controller policy.

Revision ID: 20261005_01
Revises: 20260924_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20261005_01"
down_revision = "20260924_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the singleton controller table, seeded lazily by the API disabled."""
    op.create_table(
        "account_research_controller",
        sa.Column("controller_id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("configuration", sa.JSON()),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_enqueued_job_id", sa.String(36)),
        sa.Column("last_error", sa.Text()),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    """Remove only the controller policy; research jobs remain durable history."""
    op.drop_table("account_research_controller")
