"""Persist shared search responses and fenced refresh leases.

Revision ID: 20261006_01
Revises: 20261005_01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261006_01"
down_revision = "20261005_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the shared cache without deleting existing research artifacts."""
    op.create_table(
        "search_cache",
        sa.Column("cache_key", sa.String(64), primary_key=True),
        sa.Column("request", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("response", sa.JSON().with_variant(JSONB(), "postgresql")),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("failure_until", sa.DateTime(timezone=True)),
        sa.Column("upstream_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coalesced_requests", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Remove cached discovery responses and their usage counters."""
    op.drop_table("search_cache")
