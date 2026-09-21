"""Add fenced ownership and durable workflow stage execution columns.

Revision ID: 20260918_01
Revises: 20260915_01
"""

import sqlalchemy as sa
from alembic import op

revision = "20260918_01"
down_revision = "20260915_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Extend jobs and create workflow/checkpoint tables."""
    for name, column in (
        ("workflow_id", sa.String(36)), ("stage_key", sa.String(100)),
        ("input_hash", sa.String(64)), ("owner_worker_token", sa.String(36)),
        ("deadline_at", sa.DateTime(timezone=True)), ("stage_started_at", sa.DateTime(timezone=True)),
        ("failure_code", sa.String(64)), ("result_id", sa.String(36)),
        ("resumed_from_job_id", sa.String(36)),
    ):
        op.add_column("research_jobs", sa.Column(name, column, nullable=True))
    op.alter_column("research_jobs", "kind", type_=sa.String(32), existing_type=sa.String(16))
    op.create_unique_constraint("uq_research_jobs_workflow_stage_input", "research_jobs", ["workflow_id", "stage_key", "input_hash"])
    op.create_table(
        "research_workflows",
        sa.Column("workflow_id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("parent_workflow_id", sa.String(36)),
        sa.Column("campaign_id", sa.String(80)), sa.Column("icp_id", sa.String(80)),
        sa.Column("candidate_id", sa.String(36)), sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False), sa.Column("schema_version", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("llm_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("search_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetch_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("followup_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resumed_from_workflow_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "research_stage_results",
        sa.Column("stage_result_id", sa.String(36), primary_key=True),
        sa.Column("workflow_id", sa.String(36), nullable=False), sa.Column("job_id", sa.String(36)),
        sa.Column("stage_key", sa.String(100), nullable=False), sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False), sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(64)), sa.Column("unknowns", sa.JSON(), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=False), sa.Column("trace_run_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("workflow_id", "stage_key", "revision"),
    )


def downgrade() -> None:
    """Remove workflow and stage execution additions."""
    op.drop_table("research_stage_results")
    op.drop_table("research_workflows")
    op.drop_constraint("uq_research_jobs_workflow_stage_input", "research_jobs", type_="unique")
    op.alter_column("research_jobs", "kind", type_=sa.String(16), existing_type=sa.String(32))
    for name in ("workflow_id", "stage_key", "input_hash", "owner_worker_token", "deadline_at", "stage_started_at", "failure_code", "result_id", "resumed_from_job_id"):
        op.drop_column("research_jobs", name)
