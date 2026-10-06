"""Persist bounded tool-request telemetry and collection coverage."""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "20261006_02"
down_revision = "20261006_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create telemetry tables and seed truthful collection start."""
    op.create_table(
        "tool_request_stats",
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.String(80)), sa.Column("run_id", sa.String(80)),
        sa.Column("worker_token", sa.String(80)), sa.Column("source", sa.String(20), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False), sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False), sa.Column("coalesced", sa.Boolean(), nullable=False),
        sa.Column("outbound_attempted", sa.Boolean(), nullable=False), sa.Column("http_status", sa.Integer()),
        sa.Column("reason_code", sa.String(80)), sa.Column("result_count", sa.Integer()),
        sa.CheckConstraint("duration_ms >= 0"),
    )
    op.create_index("ix_tool_request_stats_finished_at", "tool_request_stats", ["finished_at"])
    op.create_index("ix_tool_request_source_finished", "tool_request_stats", ["source", "finished_at"])
    op.create_table(
        "tool_request_issues", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.String(36), sa.ForeignKey("tool_request_stats.request_id", ondelete="CASCADE"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(20), nullable=False), sa.Column("engine", sa.String(80)),
        sa.Column("category", sa.String(10), nullable=False), sa.Column("message", sa.String(200), nullable=False),
    )
    op.create_index("ix_tool_request_issues_occurred_at", "tool_request_issues", ["occurred_at"])
    op.create_index("uq_tool_issue_dedup", "tool_request_issues", ["request_id", "source", sa.text("coalesce(engine, '')"), "message"], unique=True)
    op.create_table("stats_state", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("collection_started_at", sa.DateTime(timezone=True), nullable=False), sa.CheckConstraint("id = 1"))
    op.get_bind().execute(sa.text("INSERT INTO stats_state (id, collection_started_at) VALUES (1, :now)"), {"now": datetime.now(UTC)})
    op.create_index("ix_agent_runs_scope_started_at", "agent_runs", ["scope", "started_at"])
    op.create_index("ix_agent_runs_scope_finished_at", "agent_runs", ["scope", "finished_at"])


def downgrade() -> None:
    """Remove only the telemetry schema additions."""
    op.drop_index("ix_agent_runs_scope_finished_at", table_name="agent_runs")
    op.drop_index("ix_agent_runs_scope_started_at", table_name="agent_runs")
    op.drop_table("stats_state")
    op.drop_index("ix_tool_request_issues_occurred_at", table_name="tool_request_issues")
    op.drop_index("uq_tool_issue_dedup", table_name="tool_request_issues")
    op.drop_table("tool_request_issues")
    op.drop_index("ix_tool_request_source_finished", table_name="tool_request_stats")
    op.drop_index("ix_tool_request_stats_finished_at", table_name="tool_request_stats")
    op.drop_table("tool_request_stats")
