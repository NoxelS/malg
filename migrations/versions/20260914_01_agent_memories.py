"""Add PostgreSQL-backed NOOA agent memory."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "20260914_01"
down_revision = "20260913_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Install pgvector and durable memory tables and indexes."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "agent_memories",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("importance", sa.Float, nullable=False),
        sa.Column("salience", sa.Float, nullable=False),
        sa.Column("strength", sa.Integer, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("last_accessed_at", sa.Float, nullable=False),
        sa.Column("access_count", sa.Integer, nullable=False),
        sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("owner", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.String(16)),
        sa.Column("embedding", Vector(256)),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_agent_memories_owner_archived", "agent_memories", ["owner", "archived"])
    op.create_index("ix_agent_memories_content", "agent_memories", ["content"])
    op.execute(
        "CREATE INDEX ix_agent_memories_embedding_ivfflat ON agent_memories USING ivfflat (embedding vector_cosine_ops)"
    )
    op.create_table(
        "agent_memory_edges",
        sa.Column("source_id", sa.String(64), primary_key=True),
        sa.Column("target_id", sa.String(64), primary_key=True),
        sa.Column("type", sa.String(32), primary_key=True),
        sa.Column("weight", sa.Float, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_index("ix_agent_memory_edges_source", "agent_memory_edges", ["source_id"])
    op.create_index("ix_agent_memory_edges_target", "agent_memory_edges", ["target_id"])
    op.create_table(
        "agent_memory_maintenance",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.Float, nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("report", sa.JSON, nullable=False),
    )


def downgrade() -> None:
    """Remove durable memory tables."""
    op.drop_table("agent_memory_maintenance")
    op.drop_index("ix_agent_memory_edges_target", table_name="agent_memory_edges")
    op.drop_index("ix_agent_memory_edges_source", table_name="agent_memory_edges")
    op.drop_table("agent_memory_edges")
    op.execute("DROP INDEX IF EXISTS ix_agent_memories_embedding_ivfflat")
    op.drop_index("ix_agent_memories_content", table_name="agent_memories")
    op.drop_index("ix_agent_memories_owner_archived", table_name="agent_memories")
    op.drop_table("agent_memories")
