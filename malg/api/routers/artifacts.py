"""Campaign and ICP CRUD routes backed by canonical JSON artifacts."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from malg.core.models.campaign import CampaignCandidate
from malg.core.models.icp import ICPResult
from malg.database.models import ICP, Campaign

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


@router.post("/campaigns", response_model=CampaignCandidate, status_code=status.HTTP_201_CREATED)
def create_campaign(payload: CampaignCandidate, session: SessionDependency) -> CampaignCandidate:
    """Persist a new canonical campaign, rejecting an existing campaign ID."""
    session.add(
        Campaign(
            campaign_id=payload.campaign_id,
            title=payload.title,
            payload=payload.model_dump(mode="json"),
        )
    )
    _commit(session, conflict_detail="campaign already exists")
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
    session.add(
        ICP(
            campaign_id=campaign_id,
            icp_id=payload.icp_id,
            segment_key=payload.identity.segment_key(),
            title=payload.title,
            payload=payload.model_dump(mode="json"),
        )
    )
    _commit(session, conflict_detail="ICP ID or segment already exists for campaign")
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
