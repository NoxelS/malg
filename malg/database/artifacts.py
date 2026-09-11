"""Persistence operations used by non-HTTP MALG entry points.

The command-line research run remains responsible for agent orchestration. This
module only stores already-validated artifacts using the same canonical schema
as the API.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.orm import Session, sessionmaker

from malg.core.models.campaign import CampaignCandidate
from malg.core.models.icp import ICPResult
from malg.database.models import ICP, Campaign


def persist_icps(
    campaign: CampaignCandidate,
    icps: Iterable[ICPResult],
    session_factory: sessionmaker[Session],
) -> None:
    """Store validated ICPs from a run in one transaction.

    Args:
        campaign: The saved campaign artifact that scopes the ICPs. It is
            inserted only when absent because the relational schema requires
            an ICP parent; this does not start campaign research or updates.
        icps: Validated ICP artifacts to store.
        session_factory: Factory bound to an already-migrated database.

    Raises:
        ValueError: If an ICP belongs to another campaign.
        sqlalchemy.exc.IntegrityError: If an ICP ID or segment already exists.
    """
    icp_list = list(icps)
    if any(icp.campaign_id != campaign.campaign_id for icp in icp_list):
        raise ValueError("Every persisted ICP must belong to the saved campaign.")

    with session_factory.begin() as session:
        if session.get(Campaign, campaign.campaign_id) is None:
            session.add(
                Campaign(
                    campaign_id=campaign.campaign_id,
                    title=campaign.title,
                    payload=campaign.model_dump(mode="json"),
                )
            )
        session.add_all(
            [
                ICP(
                    campaign_id=icp.campaign_id,
                    icp_id=icp.icp_id,
                    segment_key=icp.identity.segment_key(),
                    title=icp.title,
                    payload=icp.model_dump(mode="json"),
                )
                for icp in icp_list
            ]
        )
