"""Database schema contracts independent of a running PostgreSQL server."""

from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from malg.database import Base
from malg.database.models import AgentMemory, ResearchJob


def test_persistence_schema_retains_operational_history_only() -> None:
    """The declarative metadata cannot recreate retired local CRM tables."""
    tables = {table.name: table for table in Base.metadata.sorted_tables}
    retired = {
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
    }
    assert retired.isdisjoint(tables)
    assert {
        "research_workflows",
        "research_stage_results",
        "research_sources",
        "source_fetches",
        "research_claims",
        "research_jobs",
        "crm_write_operations",
        "agent_runs",
        "agent_turns",
        "agent_trace_events",
        "worker_heartbeats",
        "agent_memories",
        "agent_memory_edges",
        "agent_memory_maintenance",
    } <= tables.keys()

    memory_ddl = str(CreateTable(tables["agent_memories"]).compile(dialect=postgresql.dialect()))
    assert "VECTOR(256)" in memory_ddl


def test_operational_history_and_memory_archive_survive_retirement() -> None:
    """Operational rows remain readable while archived business memory is hidden by default."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        job = ResearchJob(job_id="job-1", kind="campaign", status="succeeded")
        active = AgentMemory(
            id="active",
            type="fact",
            content="operational",
            importance=1.0,
            salience=1.0,
            strength=1,
            created_at=1.0,
            last_accessed_at=1.0,
            access_count=0,
            owner="ops",
            payload={},
        )
        archived = AgentMemory(
            id="archived",
            type="fact",
            content="legacy campaign",
            importance=1.0,
            salience=1.0,
            strength=1,
            created_at=1.0,
            last_accessed_at=1.0,
            access_count=0,
            archived=True,
            owner="campaign-research",
            payload={},
        )
        session.add_all([job, active, archived])
        session.commit()
        assert session.scalar(select(ResearchJob.job_id)) == "job-1"
        visible = session.scalars(select(AgentMemory).where(~AgentMemory.archived)).all()
        assert [item.id for item in visible] == ["active"]
        assert session.get(AgentMemory, "archived").archived is True
