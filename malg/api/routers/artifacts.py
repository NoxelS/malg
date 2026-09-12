"""Campaign and ICP CRUD routes backed by canonical JSON artifacts."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
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
from malg.database.artifacts import persist_account_candidate, persist_campaign, persist_icp
from malg.database.models import (
    ICP,
    Account,
    AccountMatch,
    AccountValidationRun,
    Campaign,
    CommunicationEndpoint,
    Employment,
)

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
