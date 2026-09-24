"""SQLAlchemy mappings for operational jobs, evidence, traces, and memory."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONPayload = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    """Base class for MALG's SQLAlchemy mappings."""


class ResearchWorkflow(Base):
    """Persisted workflow input, shared deadline, counters, and lifecycle."""

    __tablename__ = "research_workflows"
    workflow_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_workflow_id: Mapped[str | None] = mapped_column(String(36))
    campaign_id: Mapped[str | None] = mapped_column(String(80))
    icp_id: Mapped[str | None] = mapped_column(String(80))
    candidate_id: Mapped[str | None] = mapped_column(String(36))
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False, default=2, server_default="2")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    llm_attempts: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    search_attempts: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    fetch_attempts: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    followup_used: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    resumed_from_workflow_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchStageResult(Base):
    """Immutable result of one independently persisted workflow stage."""

    __tablename__ = "research_stage_results"
    __table_args__ = (UniqueConstraint("workflow_id", "stage_key", "revision"),)
    stage_result_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(36))
    stage_key: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    unknowns: Mapped[list[Any]] = mapped_column(JSONPayload, nullable=False, default=list)
    source_refs: Mapped[list[Any]] = mapped_column(JSONPayload, nullable=False, default=list)
    trace_run_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResearchSource(Base):
    """Canonical source identity shared by immutable fetch observations."""

    __tablename__ = "research_sources"
    source_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    canonical_url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SourceFetch(Base):
    """Append-only outcome of one bounded source retrieval."""

    __tablename__ = "source_fetches"
    fetch_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow_id: Mapped[str | None] = mapped_column(String(36))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text)
    status_code: Mapped[int | None] = mapped_column()
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    extractor_version: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(Text)
    excerpts: Mapped[list[Any]] = mapped_column(JSONPayload, nullable=False, default=list)
    failure_detail: Mapped[str | None] = mapped_column(Text)


class ResearchClaim(Base):
    """Host-validated observation or inference linked to source excerpts."""

    __tablename__ = "research_claims"
    claim_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt_refs: Mapped[list[Any]] = mapped_column(JSONPayload, nullable=False, default=list)
    supporting_claim_refs: Mapped[list[Any]] = mapped_column(
        JSONPayload, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResearchJob(Base):
    """A durable claimable unit of one bounded research stage."""

    __tablename__ = "research_jobs"
    __table_args__ = (
        Index("ix_research_jobs_status_created_at", "status", "created_at"),
        Index("ix_research_jobs_claim_expires_at", "claim_expires_at"),
        UniqueConstraint("workflow_id", "stage_key", "input_hash"),
    )

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    campaign_id: Mapped[str | None] = mapped_column(String(80))
    icp_id: Mapped[str | None] = mapped_column(String(80))
    account_id: Mapped[str | None] = mapped_column(String(36))
    person_id: Mapped[str | None] = mapped_column(String(36))
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONPayload)
    contract_version: Mapped[int | None] = mapped_column()
    contract_hash: Mapped[str | None] = mapped_column(String(64))
    result_outcome: Mapped[str | None] = mapped_column(String(32))
    result_refs: Mapped[list[Any]] = mapped_column(JSONPayload, nullable=False, default=list)
    data_origin: Mapped[str] = mapped_column(
        String(16), nullable=False, default="twenty", server_default="twenty"
    )
    workflow_id: Mapped[str | None] = mapped_column(String(36))
    stage_key: Mapped[str | None] = mapped_column(String(100))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    owner_worker_token: Mapped[str | None] = mapped_column(String(36))
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    attempt_window_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    claim_token: Mapped[str | None] = mapped_column(String(128))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stage_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_detail: Mapped[str | None] = mapped_column(Text)
    result_id: Mapped[str | None] = mapped_column(String(36))
    resumed_from_job_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    agent_runs: Mapped[list[AgentRun]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class CrmWriteOperation(Base):
    """Fenced intent and confirmation journal for one remote CRM field write."""

    __tablename__ = "crm_write_operations"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "stage_key",
            "object_name",
            "record_id",
            "field_key",
            name="uq_crm_write_operation_identity",
        ),
        Index("ix_crm_write_operations_status", "status"),
    )

    operation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("research_jobs.job_id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[str | None] = mapped_column(String(36))
    stable_operation_key: Mapped[str] = mapped_column(String(512), nullable=False)
    stage_key: Mapped[str] = mapped_column(String(100), nullable=False)
    object_name: Mapped[str] = mapped_column(String(64), nullable=False)
    record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    field_key: Mapped[str] = mapped_column(String(128), nullable=False)
    contract_version: Mapped[int] = mapped_column(nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    intended_fields: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)
    observed_record_version: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="prepared")
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    sanitized_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AgentRun(Base):
    """One authenticated worker or generated-agent execution scope."""

    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_job_started_at", "job_id", "started_at"),)
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("research_jobs.job_id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    worker_token: Mapped[str | None] = mapped_column(String(36))
    agent_name: Mapped[str] = mapped_column(String(255), nullable=False)
    method_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_traceback: Mapped[str | None] = mapped_column(Text)
    job: Mapped[ResearchJob] = relationship(back_populates="agent_runs")
    turns: Mapped[list[AgentTurn]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    events: Mapped[list[AgentTraceEvent]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class AgentTurn(Base):
    """One complete LLM request attempt within an agent run."""

    __tablename__ = "agent_turns"
    __table_args__ = (
        UniqueConstraint("run_id", "generation_id", "turn_number"),
        Index("ix_agent_turns_run_turn", "run_id", "turn_number"),
    )
    turn_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    generation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    turn_number: Mapped[int] = mapped_column(nullable=False)
    method_name: Mapped[str] = mapped_column(String(255), nullable=False)
    strategy: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    success: Mapped[bool | None] = mapped_column()
    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_traceback: Mapped[str | None] = mapped_column(Text)
    request_messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONPayload, nullable=False)
    request_params: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONPayload)
    run: Mapped[AgentRun] = relationship(back_populates="turns")


class AgentTraceEvent(Base):
    """One ordered, append-only journal entry for an agent run."""

    __tablename__ = "agent_trace_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        Index("ix_agent_trace_events_run_sequence", "run_id", "sequence"),
    )
    event_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_turns.turn_id", ondelete="SET NULL")
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)
    run: Mapped[AgentRun] = relationship(back_populates="events")
    turn: Mapped[AgentTurn | None] = relationship()


class WorkerHeartbeat(Base):
    """Latest durable liveness timestamp for one worker process."""

    __tablename__ = "worker_heartbeats"
    __table_args__ = (Index("ix_worker_heartbeats_last_seen_at", "last_seen_at"),)

    worker_token: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentMemory(Base):
    """Durable NOOA memory record stored in PostgreSQL."""

    __tablename__ = "agent_memories"
    __table_args__ = (
        Index("ix_agent_memories_owner_archived", "owner", "archived"),
        Index("ix_agent_memories_content", "content"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(nullable=False)
    salience: Mapped[float] = mapped_column(nullable=False)
    strength: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[float] = mapped_column(nullable=False)
    last_accessed_at: Mapped[float] = mapped_column(nullable=False)
    access_count: Mapped[int] = mapped_column(nullable=False)
    archived: Mapped[bool] = mapped_column(nullable=False, default=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str | None] = mapped_column(String(16))
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(256).with_variant(JSON, "sqlite"), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)


class AgentMemoryEdge(Base):
    """Directed typed association; targets may be dangling."""

    __tablename__ = "agent_memory_edges"
    __table_args__ = (
        UniqueConstraint("source_id", "target_id", "type", name="uq_agent_memory_edges"),
        Index("ix_agent_memory_edges_source", "source_id"),
        Index("ix_agent_memory_edges_target", "target_id"),
    )
    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), primary_key=True)
    weight: Mapped[float] = mapped_column(nullable=False)
    created_at: Mapped[float] = mapped_column(nullable=False)


class AgentMemoryMaintenance(Base):
    """Append-only maintenance reports for the memory subsystem."""

    __tablename__ = "agent_memory_maintenance"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[float] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSONPayload, nullable=False)
