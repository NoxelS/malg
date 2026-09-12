"""Add durable research job claims and artifact references.

Revision ID: 20260913_01
Revises: 20260912_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260913_01"
down_revision = "20260912_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the durable job table and claim indexes."""
    op.create_table(
        "research_jobs",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("campaign_id", sa.String(length=80)),
        sa.Column("icp_id", sa.String(length=80)),
        sa.Column("account_match_id", sa.String(length=36)),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("claim_token", sa.String(length=128)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("failure_detail", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index("ix_research_jobs_status_created_at", "research_jobs", ["status", "created_at"])
    op.create_index("ix_research_jobs_claim_expires_at", "research_jobs", ["claim_expires_at"])


def downgrade() -> None:
    """Drop durable jobs."""
    op.drop_index("ix_research_jobs_claim_expires_at", table_name="research_jobs")
    op.drop_index("ix_research_jobs_status_created_at", table_name="research_jobs")
    op.drop_table("research_jobs")
