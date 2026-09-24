"""Finish operational Twenty ownership after the guarded destructive revision.

Revision ID: 20260924_01
Revises: 20260923_02

The predecessor owns destructive authorization. This additive successor never
reclassifies Twenty-origin jobs or grants authorization to execute offline SQL.
"""

import sqlalchemy as sa
from alembic import context, op

revision = "20260924_01"
down_revision = "20260923_02"
branch_labels = None
depends_on = None

_RETIRED = (
    "campaigns",
    "icps",
    "accounts",
    "account_matches",
    "contacts",
    "employments",
    "communication_endpoints",
    "account_validation_runs",
    "artifact_versions",
    "leads",
    "lead_reviews",
)


def upgrade() -> None:
    """Preserve immutable history while removing obsolete operational references."""
    offline = context.is_offline_mode()
    if offline:
        op.execute(
            "-- Execution requires maintenance, verified encrypted restore, and the same cutover authorization as online migration."
        )
        columns = set()
    else:
        inspector = sa.inspect(op.get_bind())
        if set(inspector.get_table_names()).intersection(_RETIRED):
            raise RuntimeError("retired business tables remain after the destructive revision")
        columns = {column["name"] for column in inspector.get_columns("research_jobs")}
    if "result_outcome" not in columns:
        op.add_column("research_jobs", sa.Column("result_outcome", sa.String(32)))
    if "attempt_window_count" not in columns:
        op.add_column(
            "research_jobs",
            sa.Column("attempt_window_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if offline or "account_match_id" in columns:
        op.drop_column("research_jobs", "account_match_id")
    op.alter_column(
        "research_jobs",
        "data_origin",
        server_default="twenty",
        existing_type=sa.String(16),
        existing_nullable=False,
    )
    op.execute(
        sa.text("""
        UPDATE research_jobs
        SET status = 'cancelled', finished_at = COALESCE(finished_at, now()),
            claim_token = NULL, owner_worker_token = NULL, claim_expires_at = NULL,
            claimed_at = NULL, failure_detail = COALESCE(failure_detail, 'legacy local CRM cutover')
        WHERE data_origin = 'legacy_local' AND status IN ('queued', 'running')
    """)
    )
    op.execute(
        sa.text("""
        UPDATE research_jobs SET contract_version = NULL, contract_hash = NULL,
            result_refs = '[]'::json
        WHERE data_origin = 'legacy_local'
    """)
    )
    op.execute(
        sa.text("""
        UPDATE agent_memories SET archived = TRUE
        WHERE owner = 'campaign-research' OR owner LIKE 'campaign-research@%'
    """)
    )
    # Offline output performs the same final invariant check when authorized SQL runs.
    if offline:
        names = ", ".join("'" + name + "'" for name in _RETIRED)
        op.execute(f"""DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM information_schema.tables
                     WHERE table_schema = current_schema() AND table_name IN ({names})) THEN
            RAISE EXCEPTION 'retired business tables remain';
          END IF;
        END $$""")


def downgrade() -> None:
    """Require backup restoration with writers stopped; never recreate empty mirrors."""
    raise RuntimeError("CRM cutover restoration requires the verified pre-cutover database backup")
