"""Transactional workflow and immutable stage-result transitions."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from malg.database.jobs import require_claim
from malg.database.models import ResearchStageResult, ResearchWorkflow


def canonical_input_hash(payload: Any) -> str:
    """Hash canonical JSON so recovery can reuse identical stage inputs."""
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def create_workflow_for_job(session: Session, job: Any, payload: dict[str, Any], deadline_at: datetime) -> ResearchWorkflow:
    """Create a pinned workflow for a newly claimed stage."""
    workflow = ResearchWorkflow(workflow_id=str(uuid4()), kind=job.kind, campaign_id=job.campaign_id, icp_id=job.icp_id, input_payload=payload, input_hash=canonical_input_hash(payload), schema_version=2, status="running", started_at=datetime.now(UTC), deadline_at=deadline_at)
    session.add(workflow)
    session.flush()
    job.workflow_id = workflow.workflow_id
    job.stage_key = f"{job.kind}.brief"
    job.input_hash = workflow.input_hash
    job.deadline_at = deadline_at
    return workflow


def persist_stage_result(session: Session, *, job_id: str, claim_token: str, workflow_id: str, stage_key: str, input_hash: str, outcome: str, payload: dict[str, Any], now: datetime, unknowns: list[str] | None = None, source_refs: list[str] | None = None, reason_code: str | None = None) -> ResearchStageResult:
    """Fence the job and append one immutable stage result."""
    require_claim(session, job_id, claim_token, now)
    workflow = session.scalar(select(ResearchWorkflow).where(ResearchWorkflow.workflow_id == workflow_id).with_for_update())
    if workflow is None:
        raise ValueError("workflow does not exist")
    latest = session.scalar(select(ResearchStageResult).where(ResearchStageResult.workflow_id == workflow_id, ResearchStageResult.stage_key == stage_key).order_by(ResearchStageResult.revision.desc()).limit(1))
    if latest is not None and latest.input_hash == input_hash:
        return latest
    result = ResearchStageResult(stage_result_id=str(uuid4()), job_id=job_id, workflow_id=workflow_id, stage_key=stage_key, schema_version=2, revision=1 if latest is None else latest.revision + 1, input_hash=input_hash, outcome=outcome, payload=payload, reason_code=reason_code, unknowns=unknowns or [], source_refs=source_refs or [])
    session.add(result)
    session.flush()
    return result
