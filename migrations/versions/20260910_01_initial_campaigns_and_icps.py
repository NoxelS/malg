"""Create canonical campaign and ICP persistence tables.

Revision ID: 20260910_01
Revises:
Create Date: 2026-09-10
"""

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "20260910_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create artifact tables or adopt the compatible pre-Alembic baseline.

    The first persistence slice created these tables before Alembic was added.
    A deployment with both tables present is therefore a compatible baseline:
    this no-op lets Alembic write its version marker without destroying data.
    A partially present schema remains an error rather than being guessed at.
    """
    if context.is_offline_mode():
        _create_tables()
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    has_campaigns = inspector.has_table("campaigns")
    has_icps = inspector.has_table("icps")
    if has_campaigns and has_icps:
        return
    if has_campaigns or has_icps:
        raise RuntimeError(
            "Cannot adopt a partial MALG persistence schema; expected both campaigns and icps."
        )
    _create_tables()


def _create_tables() -> None:
    """Emit the canonical table DDL when no compatible baseline is present."""
    op.create_table(
        "campaigns",
        sa.Column("campaign_id", sa.String(length=80), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("campaign_id"),
    )
    op.create_table(
        "icps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.String(length=80), nullable=False),
        sa.Column("icp_id", sa.String(length=80), nullable=False),
        sa.Column("segment_key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.campaign_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "icp_id", name="uq_icps_campaign_id_icp_id"),
        sa.UniqueConstraint("campaign_id", "segment_key", name="uq_icps_campaign_id_segment_key"),
    )


def downgrade() -> None:
    """Remove API-owned artifact tables in dependency order."""
    op.drop_table("icps")
    op.drop_table("campaigns")
