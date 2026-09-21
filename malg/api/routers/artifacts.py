"""Campaign and ICP CRUD routes backed by canonical JSON artifacts."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from nooa_memory.embeddings import HashingEmbedder  # type: ignore[import-untyped]
from nooa_memory.schema import Memory  # type: ignore[import-untyped]
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from malg.core.models.account import (
    AccountCandidate,
    AccountMatchRecord,
    AccountRecord,
    AccountValidationAssessment,
    CommunicationEndpointCandidate,
    ContactCandidate,
)
from malg.core.models.campaign import CampaignCandidate
from malg.core.models.icp import ICPResult
from malg.core.models.memory import MemoryPage
from malg.database.artifacts import persist_account_candidate, persist_campaign, persist_icp
from malg.database.memory import PostgresMemoryStore
from malg.database.models import (
    ICP,
    Account,
    AccountMatch,
    AccountValidationRun,
    ArtifactVersion,
    Campaign,
    CommunicationEndpoint,
    Employment,
    Lead,
    LeadReview,
)
from malg.database.session import make_session_factory

router = APIRouter(prefix="/api/v1", tags=["artifacts"])


def get_session() -> Generator[Session]:
    """Declare the session boundary that the application factory configures."""
    raise RuntimeError("The MALG application did not configure a database session.")
    yield


SessionDependency = Annotated[Session, Depends(get_session)]


def _campaign_or_404(session: Session, campaign_id: str) -> Campaign:
    """Return a persisted campaign or raise the API's stable not-found response."""
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="campaign not found")
    return campaign


def _icp_or_404(session: Session, campaign_id: str, icp_id: str) -> ICP:
    """Return a campaign-scoped ICP or raise the API's stable not-found response."""
    icp = session.scalar(select(ICP).where(ICP.campaign_id == campaign_id, ICP.icp_id == icp_id))
    if icp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="icp not found")
    return icp


def _account_or_404(session: Session, account_id: str) -> Account:
    """Return a global account or raise the API's stable not-found response."""
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return account


def _account_match_or_404(
    session: Session, campaign_id: str, icp_id: str, account_id: str
) -> AccountMatch:
    """Return one campaign/ICP-scoped account match or raise a stable response."""
    match = session.scalar(
        select(AccountMatch).where(
            AccountMatch.campaign_id == campaign_id,
            AccountMatch.icp_id == icp_id,
            AccountMatch.account_id == account_id,
        )
    )
    if match is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account match not found")
    return match


def _commit(session: Session, *, conflict_detail: str) -> None:
    """Commit a write, translating database uniqueness errors into HTTP conflicts."""
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=conflict_detail) from error


def _campaign_response(campaign: Campaign) -> CampaignCandidate:
    """Validate and return the canonical campaign JSON stored in one row."""
    return CampaignCandidate.model_validate(campaign.payload)


def _icp_response(icp: ICP) -> ICPResult:
    """Validate and return the canonical ICP JSON stored in one row."""
    return ICPResult.model_validate(icp.payload)


def _account_response(account: Account) -> AccountRecord:
    """Return the canonical global account profile stored in one row."""
    return AccountRecord.model_validate(
        {
            "account_id": account.account_id,
            **account.payload,
            "created_at": account.created_at,
            "updated_at": account.updated_at,
        }
    )


def _account_match_response(session: Session, match: AccountMatch) -> AccountMatchRecord:
    """Return an account match with its latest append-only validation assessment."""
    validation_run = session.scalar(
        select(AccountValidationRun)
        .where(AccountValidationRun.account_match_id == match.account_match_id)
        .order_by(AccountValidationRun.created_at.desc())
    )
    return AccountMatchRecord.model_validate(
        {
            "account_match_id": match.account_match_id,
            "account_id": match.account_id,
            "candidate": match.payload,
            "validation": validation_run.payload if validation_run is not None else None,
            "created_at": match.created_at,
            "updated_at": match.updated_at,
        }
    )


def _memory_store(session: Session) -> PostgresMemoryStore:
    """Build the shared memory store from the request's configured engine."""
    return PostgresMemoryStore(make_session_factory(cast(Engine, session.get_bind())))


@router.post("/memories", response_model=Memory, status_code=status.HTTP_201_CREATED)
def create_memory(payload: Memory, session: SessionDependency) -> Memory:
    """Create one administrative memory record and its outgoing edges."""
    store = _memory_store(session)
    if store.get(payload.id) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="memory already exists")
    try:
        return store.add(payload, HashingEmbedder().embed(payload.embedding_text()))
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="memory already exists"
        ) from error


@router.get("/memories", response_model=MemoryPage)
def list_memories(
    session: SessionDependency,
    owner: str | None = None,
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> MemoryPage:
    """List memories with exact owner filtering and stable pagination."""
    if not 1 <= limit <= 100 or offset < 0:
        raise HTTPException(status_code=422, detail="invalid pagination")
    store = _memory_store(session)
    items = store.all_memories(include_archived=include_archived, owner=owner)
    if owner is not None:
        items = [item for item in items if item.owner == owner]
    return MemoryPage(
        items=items[offset : offset + limit], total=len(items), limit=limit, offset=offset
    )


def _memory_or_404(store: PostgresMemoryStore, memory_id: str) -> Memory:
    """Return one memory or the stable administrative not-found response."""
    memory = store.get(memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return memory


@router.get("/memories/{memory_id}", response_model=Memory)
def get_memory(memory_id: str, session: SessionDependency) -> Memory:
    """Return one durable memory record."""
    return _memory_or_404(_memory_store(session), memory_id)


@router.put("/memories/{memory_id}", response_model=Memory)
def replace_memory(memory_id: str, payload: Memory, session: SessionDependency) -> Memory:
    """Replace a memory, re-embedding only when content changes."""
    if payload.id != memory_id:
        raise HTTPException(status_code=422, detail="memory ID mismatch")
    store = _memory_store(session)
    existing = _memory_or_404(store, memory_id)
    embedding = (
        HashingEmbedder().embed(payload.embedding_text())
        if payload.content != existing.content
        else None
    )
    try:
        if embedding is None:
            store.save(payload)
        else:
            store.add(payload, embedding)
    except IntegrityError as error:
        raise HTTPException(status_code=409, detail="memory update conflict") from error
    return payload


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: str, session: SessionDependency) -> Response:
    """Physically delete a memory and all inbound and outbound edges."""
    store = _memory_store(session)
    _memory_or_404(store, memory_id)
    store.delete(memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/campaigns", response_model=CampaignCandidate, status_code=status.HTTP_201_CREATED)
def create_campaign(payload: CampaignCandidate, session: SessionDependency) -> CampaignCandidate:
    """Persist a new canonical campaign, rejecting an existing campaign ID."""
    try:
        persist_campaign(payload, session)
        _commit(session, conflict_detail="campaign already exists")
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="campaign already exists"
        ) from error
    return payload


@router.get("/campaigns", response_model=list[CampaignCandidate])
def list_campaigns(session: SessionDependency) -> list[CampaignCandidate]:
    """List all persisted campaigns in stable campaign-ID order."""
    return [
        _campaign_response(row)
        for row in session.scalars(select(Campaign).order_by(Campaign.campaign_id))
    ]


@router.get("/campaigns/{campaign_id}", response_model=CampaignCandidate)
def get_campaign(campaign_id: str, session: SessionDependency) -> CampaignCandidate:
    """Fetch one persisted campaign by its host-controlled ID."""
    return _campaign_response(_campaign_or_404(session, campaign_id))


@router.put("/campaigns/{campaign_id}", response_model=CampaignCandidate)
def replace_campaign(
    campaign_id: str, payload: CampaignCandidate, session: SessionDependency
) -> CampaignCandidate:
    """Fully replace a campaign while requiring the body and path IDs to agree."""
    if payload.campaign_id != campaign_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="campaign ID mismatch"
        )
    campaign = _campaign_or_404(session, campaign_id)
    campaign.title = payload.title
    campaign.payload = payload.model_dump(mode="json")
    _commit(session, conflict_detail="campaign update conflicts with existing data")
    return payload


@router.delete("/campaigns/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_campaign(campaign_id: str, session: SessionDependency) -> Response:
    """Delete one campaign and its database-cascaded ICPs."""
    session.delete(_campaign_or_404(session, campaign_id))
    _commit(session, conflict_detail="campaign deletion conflicts with existing data")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/campaigns/{campaign_id}/icps", response_model=ICPResult, status_code=status.HTTP_201_CREATED
)
def create_icp(campaign_id: str, payload: ICPResult, session: SessionDependency) -> ICPResult:
    """Persist an ICP beneath an existing campaign with deterministic segment uniqueness."""
    if payload.campaign_id != campaign_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="campaign ID mismatch"
        )
    _campaign_or_404(session, campaign_id)
    try:
        persist_icp(payload, session)
        _commit(session, conflict_detail="ICP ID or segment already exists for campaign")
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ICP ID or segment already exists for campaign",
        ) from error
    return payload


@router.get("/campaigns/{campaign_id}/icps", response_model=list[ICPResult])
def list_icps(campaign_id: str, session: SessionDependency) -> list[ICPResult]:
    """List a campaign's persisted ICPs in stable ICP-ID order."""
    _campaign_or_404(session, campaign_id)
    rows = session.scalars(select(ICP).where(ICP.campaign_id == campaign_id).order_by(ICP.icp_id))
    return [_icp_response(row) for row in rows]


@router.get("/campaigns/{campaign_id}/icps/{icp_id}", response_model=ICPResult)
def get_icp(campaign_id: str, icp_id: str, session: SessionDependency) -> ICPResult:
    """Fetch one ICP beneath its campaign."""
    _campaign_or_404(session, campaign_id)
    return _icp_response(_icp_or_404(session, campaign_id, icp_id))


@router.put("/campaigns/{campaign_id}/icps/{icp_id}", response_model=ICPResult)
def replace_icp(
    campaign_id: str, icp_id: str, payload: ICPResult, session: SessionDependency
) -> ICPResult:
    """Fully replace an ICP while preserving its path-scoped identity."""
    if payload.campaign_id != campaign_id or payload.icp_id != icp_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="ICP ID mismatch"
        )
    _campaign_or_404(session, campaign_id)
    icp = _icp_or_404(session, campaign_id, icp_id)
    icp.title = payload.title
    icp.segment_key = payload.identity.segment_key()
    icp.payload = payload.model_dump(mode="json")
    _commit(session, conflict_detail="ICP ID or segment already exists for campaign")
    return payload


@router.delete("/campaigns/{campaign_id}/icps/{icp_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_icp(campaign_id: str, icp_id: str, session: SessionDependency) -> Response:
    """Delete one ICP beneath an existing campaign."""
    _campaign_or_404(session, campaign_id)
    session.delete(_icp_or_404(session, campaign_id, icp_id))
    _commit(session, conflict_detail="ICP deletion conflicts with existing data")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/accounts", response_model=list[AccountRecord])
def list_accounts(session: SessionDependency) -> list[AccountRecord]:
    """List global accounts in stable display-name order without campaign inference."""
    return [
        _account_response(row)
        for row in session.scalars(select(Account).order_by(Account.display_name))
    ]


@router.get("/accounts/{account_id}", response_model=AccountRecord)
def get_account(account_id: str, session: SessionDependency) -> AccountRecord:
    """Fetch one global account identity and its current company-level profile."""
    return _account_response(_account_or_404(session, account_id))


@router.get("/accounts/{account_id}/contacts", response_model=list[ContactCandidate])
def list_account_contacts(account_id: str, session: SessionDependency) -> list[ContactCandidate]:
    """List sourced current contacts for one account without making outreach decisions."""
    _account_or_404(session, account_id)
    rows = session.scalars(
        select(Employment).where(Employment.account_id == account_id).order_by(Employment.title)
    )
    return [ContactCandidate.model_validate(row.payload) for row in rows]


@router.get(
    "/accounts/{account_id}/entrypoints", response_model=list[CommunicationEndpointCandidate]
)
def list_account_entrypoints(
    account_id: str, session: SessionDependency
) -> list[CommunicationEndpointCandidate]:
    """List account-owned public business entrypoints and their source metadata."""
    _account_or_404(session, account_id)
    rows = session.scalars(
        select(CommunicationEndpoint)
        .where(CommunicationEndpoint.account_id == account_id)
        .order_by(CommunicationEndpoint.kind, CommunicationEndpoint.value)
    )
    return [CommunicationEndpointCandidate.model_validate(row.payload) for row in rows]


@router.post(
    "/campaigns/{campaign_id}/icps/{icp_id}/accounts",
    response_model=AccountMatchRecord,
    status_code=status.HTTP_201_CREATED,
)
def create_or_refresh_account_match(
    campaign_id: str,
    icp_id: str,
    payload: AccountCandidate,
    session: SessionDependency,
) -> AccountMatchRecord:
    """Persist a sourced candidate under an existing campaign and ICP.

    This API remains persistence-only: it accepts an already-produced candidate
    but neither invokes the research agent nor initiates communication.
    """
    if payload.campaign_id != campaign_id or payload.icp_id != icp_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="campaign or ICP ID mismatch",
        )
    match = persist_account_candidate(payload, validation=None, session=session)
    _commit(session, conflict_detail="account candidate conflicts with existing data")
    return _account_match_response(session, match)


@router.get(
    "/campaigns/{campaign_id}/icps/{icp_id}/accounts", response_model=list[AccountMatchRecord]
)
def list_account_matches(
    campaign_id: str, icp_id: str, session: SessionDependency
) -> list[AccountMatchRecord]:
    """List a campaign ICP's account candidates in descending fit-score order."""
    _icp_or_404(session, campaign_id, icp_id)
    matches = session.scalars(
        select(AccountMatch)
        .where(AccountMatch.campaign_id == campaign_id, AccountMatch.icp_id == icp_id)
        .order_by(AccountMatch.fit_score.desc(), AccountMatch.account_id)
    )
    return [_account_match_response(session, match) for match in matches]


@router.post(
    "/campaigns/{campaign_id}/icps/{icp_id}/accounts/{account_id}/validations",
    response_model=AccountMatchRecord,
)
def append_account_validation(
    campaign_id: str,
    icp_id: str,
    account_id: str,
    payload: AccountValidationAssessment,
    session: SessionDependency,
) -> AccountMatchRecord:
    """Append an externally produced validation result to one existing account match."""
    match = _account_match_or_404(session, campaign_id, icp_id, account_id)
    candidate = AccountCandidate.model_validate(match.payload)
    persist_account_candidate(candidate, validation=payload, session=session)
    _commit(session, conflict_detail="account validation conflicts with existing data")
    return _account_match_response(session, match)


@router.get("/accounts/{account_id}/research")
def get_account_research(account_id: str, session: SessionDependency) -> dict[str, object]:
    """Return account matches plus persisted stage outputs without inference."""
    _account_or_404(session, account_id)
    matches = session.scalars(
        select(AccountMatch).where(AccountMatch.account_id == account_id).order_by(AccountMatch.created_at)
    )
    return {
        "account_id": account_id,
        "qualifications": [
            {"account_match_id": row.account_match_id, "campaign_id": row.campaign_id,
             "icp_id": row.icp_id, "status": row.status, "payload": row.payload}
            for row in matches
        ],
        "projects": [], "contacts": [], "leads": [],
    }


@router.get("/leads")
def list_leads(session: SessionDependency, limit: int = 50, offset: int = 0) -> dict[str, object]:
    """List host-assembled leads with stable pagination."""
    if limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(status_code=422, detail="invalid pagination")
    total = session.query(Lead).count()
    rows = session.scalars(select(Lead).order_by(Lead.updated_at.desc()).offset(offset).limit(limit))
    return {
        "items": [
            {"lead_id": row.lead_id, "workflow_id": row.workflow_id,
             "completeness": row.completeness, "review_status": row.review_status,
             "limitations": row.limitations, "payload": row.payload}
            for row in rows
        ],
        "total": total, "limit": limit, "offset": offset,
    }


@router.get("/leads/{lead_id}")
def get_lead(lead_id: str, session: SessionDependency) -> dict[str, object]:
    """Return one current lead projection."""
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found")
    return {"lead_id": lead.lead_id, "workflow_id": lead.workflow_id,
            "completeness": lead.completeness, "review_status": lead.review_status,
            "limitations": lead.limitations, "payload": lead.payload}


@router.post("/leads/{lead_id}/review")
def review_lead(lead_id: str, payload: dict[str, str], session: SessionDependency) -> dict[str, object]:
    """Record an append-only human review, rejecting incomplete acceptance."""
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found")
    decision, reason = payload.get("decision"), payload.get("reason", "")
    if decision not in {"accepted", "rejected"} or not reason:
        raise HTTPException(status_code=422, detail="decision and reason are required")
    if decision == "accepted" and lead.completeness != "complete":
        raise HTTPException(status_code=409, detail="incomplete lead cannot be accepted")
    lead.review_status = decision
    session.add(LeadReview(review_id=str(uuid4()), lead_id=lead_id, decision=decision, reason=reason, actor="api"))
    session.commit()
    return get_lead(lead_id, session)


@router.get("/artifacts/{artifact_type}/{artifact_id}/versions")
def list_artifact_versions(artifact_type: str, artifact_id: str, session: SessionDependency) -> dict[str, object]:
    """List immutable raw payload versions for one artifact."""
    rows = session.scalars(
        select(ArtifactVersion)
        .where(ArtifactVersion.artifact_type == artifact_type, ArtifactVersion.artifact_id == artifact_id)
        .order_by(ArtifactVersion.created_at, ArtifactVersion.version_id)
    )
    items = [{"version_id": row.version_id, "schema_version": row.schema_version,
              "payload": row.payload, "input_hash": row.input_hash, "created_at": row.created_at}
             for row in rows]
    return {"items": items, "total": len(items), "limit": len(items), "offset": 0}
