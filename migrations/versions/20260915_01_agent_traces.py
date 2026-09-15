"""Add durable worker and agent trace history."""

import sqlalchemy as sa
from alembic import op

revision = "20260915_01"
down_revision = "20260914_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create append-only trace hierarchy."""
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("research_jobs.job_id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("worker_token", sa.String(36)), sa.Column("agent_name", sa.String(255), nullable=False),
        sa.Column("method_name", sa.String(255), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_type", sa.String(255)), sa.Column("error_message", sa.Text), sa.Column("error_traceback", sa.Text),
    )
    op.create_index("ix_agent_runs_job_started_at", "agent_runs", ["job_id", "started_at"])
    op.create_table(
        "agent_turns",
        sa.Column("turn_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation_id", sa.String(255), nullable=False), sa.Column("turn_number", sa.Integer, nullable=False),
        sa.Column("method_name", sa.String(255), nullable=False), sa.Column("strategy", sa.String(255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("success", sa.Boolean), sa.Column("error_type", sa.String(255)), sa.Column("error_message", sa.Text), sa.Column("error_traceback", sa.Text),
        sa.Column("request_messages", sa.JSON, nullable=False), sa.Column("request_params", sa.JSON, nullable=False), sa.Column("response", sa.JSON),
        sa.UniqueConstraint("run_id", "generation_id", "turn_number"),
    )
    op.create_index("ix_agent_turns_run_turn", "agent_turns", ["run_id", "turn_number"])
    op.create_table(
        "agent_trace_events",
        sa.Column("event_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("turn_id", sa.Integer, sa.ForeignKey("agent_turns.turn_id", ondelete="SET NULL")),
        sa.Column("sequence", sa.Integer, nullable=False), sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(255), nullable=False), sa.Column("payload", sa.JSON, nullable=False),
        sa.UniqueConstraint("run_id", "sequence"),
    )
    op.create_index("ix_agent_trace_events_run_sequence", "agent_trace_events", ["run_id", "sequence"])


def downgrade() -> None:
    """Remove trace history."""
    op.drop_table("agent_trace_events")
    op.drop_table("agent_turns")
    op.drop_table("agent_runs")
