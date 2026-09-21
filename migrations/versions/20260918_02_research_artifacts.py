"""Add immutable artifact, source, and lead history.

Revision ID: 20260918_02
Revises: 20260918_01
"""
import sqlalchemy as sa
from alembic import op

revision = "20260918_02"
down_revision = "20260918_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create append-only artifact, source, and lead registries."""
    op.create_table("artifact_versions", sa.Column("version_id", sa.String(36), primary_key=True), sa.Column("artifact_type", sa.String(32), nullable=False), sa.Column("artifact_id", sa.String(80), nullable=False), sa.Column("campaign_id", sa.String(80)), sa.Column("schema_version", sa.Integer(), nullable=False), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("input_hash", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")), sa.UniqueConstraint("artifact_type", "artifact_id", "version_id"))
    op.create_table("research_sources", sa.Column("source_id", sa.String(36), primary_key=True), sa.Column("canonical_url", sa.Text(), nullable=False, unique=True), sa.Column("original_url", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.create_table("source_fetches", sa.Column("fetch_id", sa.String(36), primary_key=True), sa.Column("source_id", sa.String(36), nullable=False), sa.Column("workflow_id", sa.String(36)), sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False), sa.Column("final_url", sa.Text()), sa.Column("status_code", sa.Integer()), sa.Column("outcome", sa.String(32), nullable=False), sa.Column("content_type", sa.String(128)), sa.Column("content_hash", sa.String(64)), sa.Column("extractor_version", sa.String(32)), sa.Column("title", sa.Text()), sa.Column("excerpts", sa.JSON(), nullable=False), sa.Column("failure_detail", sa.Text()), sa.CheckConstraint("status_code IS NULL OR (status_code >= 100 AND status_code <= 999)", name="ck_source_fetches_status_code"))
    op.create_table("research_claims", sa.Column("claim_id", sa.String(36), primary_key=True), sa.Column("workflow_id", sa.String(36), nullable=False), sa.Column("kind", sa.String(16), nullable=False), sa.Column("text", sa.Text(), nullable=False), sa.Column("excerpt_refs", sa.JSON(), nullable=False), sa.Column("supporting_claim_refs", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.create_table("leads", sa.Column("lead_id", sa.String(36), primary_key=True), sa.Column("workflow_id", sa.String(36), nullable=False, unique=True), sa.Column("campaign_id", sa.String(80)), sa.Column("icp_id", sa.String(80)), sa.Column("account_match_id", sa.String(36)), sa.Column("project_id", sa.String(36)), sa.Column("contact_id", sa.String(36)), sa.Column("completeness", sa.String(40), nullable=False), sa.Column("review_status", sa.String(16), nullable=False, server_default="pending"), sa.Column("limitations", sa.JSON(), nullable=False), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.create_table("lead_reviews", sa.Column("review_id", sa.String(36), primary_key=True), sa.Column("lead_id", sa.String(36), nullable=False), sa.Column("decision", sa.String(16), nullable=False), sa.Column("reason", sa.Text(), nullable=False), sa.Column("actor", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))


def downgrade() -> None:
    """Drop provenance tables."""
    for table in ("lead_reviews", "leads", "research_claims", "source_fetches", "research_sources", "artifact_versions"):
        op.drop_table(table)
