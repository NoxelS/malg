"""Add Twenty job references and fenced CRM write intents.

Revision ID: 20260923_01
Revises: 20260918_02
"""

import sqlalchemy as sa
from alembic import op

revision = "20260923_01"
down_revision = "20260918_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = (
        sa.Column("account_id", sa.String(36)),
        sa.Column("person_id", sa.String(36)),
        sa.Column("request_payload", sa.JSON()),
        sa.Column("contract_version", sa.Integer()),
        sa.Column("contract_hash", sa.String(64)),
        sa.Column("result_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("data_origin", sa.String(16), nullable=False, server_default="legacy_local"),
    )
    for column in columns:
        op.add_column("research_jobs", column)
    op.execute(
        """
        UPDATE research_jobs
        SET
            status = 'cancelled',
            finished_at = COALESCE(finished_at, now()),
            claim_token = NULL,
            owner_worker_token = NULL,
            claim_expires_at = NULL,
            claimed_at = NULL,
            failure_detail = COALESCE(failure_detail, 'legacy local CRM cutover')
        WHERE status IN ('queued', 'running')
        """
    )
    op.create_table(
        "crm_write_operations",
        sa.Column("operation_id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("research_jobs.job_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_id", sa.String(36)),
        sa.Column("stable_operation_key", sa.String(512), nullable=False),
        sa.Column("stage_key", sa.String(100), nullable=False),
        sa.Column("object_name", sa.String(64), nullable=False),
        sa.Column("record_id", sa.String(36), nullable=False),
        sa.Column("field_key", sa.String(128), nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False),
        sa.Column("contract_hash", sa.String(64), nullable=False),
        sa.Column("intended_fields", sa.JSON(), nullable=False),
        sa.Column("observed_record_version", sa.String(128)),
        sa.Column("status", sa.String(16), nullable=False, server_default="prepared"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sanitized_error", sa.Text()),
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
        sa.UniqueConstraint("job_id", "stage_key", "object_name", "record_id", "field_key"),
    )


def downgrade() -> None:
    """Remove only additive operational structures."""
    op.drop_table("crm_write_operations")
    for name in (
        "data_origin",
        "result_refs",
        "contract_hash",
        "contract_version",
        "request_payload",
        "person_id",
        "account_id",
    ):
        op.drop_column("research_jobs", name)
