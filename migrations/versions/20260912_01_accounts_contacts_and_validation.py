"""Add account, contact, endpoint, and account-validation persistence.

Revision ID: 20260912_01
Revises: 20260910_01
Create Date: 2026-09-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260912_01"
down_revision = "20260910_01"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column[object]]:
    """Return the standard creation and update timestamps for mutable records."""
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    """Create global account identities and campaign-scoped validation history."""
    payload = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "accounts",
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("identity_key", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("primary_domain", sa.String(length=255), nullable=True),
        sa.Column("payload", payload, nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("account_id"),
        sa.UniqueConstraint("identity_key"),
    )
    op.create_table(
        "account_matches",
        sa.Column("account_match_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=80), nullable=False),
        sa.Column("icp_id", sa.String(length=80), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("fit_score", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", payload, nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.campaign_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.account_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["campaign_id", "icp_id"],
            ["icps.campaign_id", "icps.icp_id"],
            name="fk_account_matches_icp",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_match_id"),
        sa.UniqueConstraint(
            "campaign_id", "icp_id", "account_id", name="uq_account_matches_campaign_icp_account"
        ),
    )
    op.create_table(
        "contacts",
        sa.Column("contact_id", sa.String(length=36), nullable=False),
        sa.Column("identity_key", sa.String(length=1024), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("payload", payload, nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("contact_id"),
        sa.UniqueConstraint("identity_key"),
    )
    op.create_table(
        "employments",
        sa.Column("employment_id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("contact_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("buyer_role", sa.Text(), nullable=True),
        sa.Column("payload", payload, nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.account_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.contact_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("employment_id"),
        sa.UniqueConstraint("account_id", "contact_id", name="uq_employments_account_contact"),
    )
    op.create_table(
        "communication_endpoints",
        sa.Column("endpoint_id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=True),
        sa.Column("contact_id", sa.String(length=36), nullable=True),
        sa.Column("owner_key", sa.String(length=48), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.Text(), nullable=False),
        sa.Column("discovery_method", sa.String(length=32), nullable=False),
        sa.Column("payload", payload, nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "(account_id IS NOT NULL AND contact_id IS NULL) OR "
            "(account_id IS NULL AND contact_id IS NOT NULL)",
            name="ck_communication_endpoints_one_owner",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.account_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.contact_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("endpoint_id"),
        sa.UniqueConstraint("owner_key", "kind", "normalized_value", name="uq_endpoints_owner_kind_value"),
    )
    op.create_table(
        "account_validation_runs",
        sa.Column("validation_run_id", sa.String(length=36), nullable=False),
        sa.Column("account_match_id", sa.String(length=36), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("payload", payload, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_match_id"], ["account_matches.account_match_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("validation_run_id"),
    )


def downgrade() -> None:
    """Remove account persistence in dependency order."""
    op.drop_table("account_validation_runs")
    op.drop_table("communication_endpoints")
    op.drop_table("employments")
    op.drop_table("contacts")
    op.drop_table("account_matches")
    op.drop_table("accounts")
