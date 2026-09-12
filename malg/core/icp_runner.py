"""Host-owned orchestration for bounded, database-backed ICP batches."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from malg.config import ICPConfig, get_icp_config, load_settings
from malg.core.agents.icp_research import ICPResearchAgent
from malg.core.browser_support import aclose_browser
from malg.core.models.campaign import CampaignCandidate
from malg.core.models.icp import ICPBatchResult, ICPIdentity, ICPRejection, ICPResult
from malg.database.artifacts import persist_icp
from malg.database.models import ICP
from malg.database.session import make_engine, make_session_factory
from malg.utils.console_progress import ConsoleProgress


class ICPResearcher(Protocol):
    """Narrow generated-agent boundary for one ICP candidate."""

    async def research_one(
        self, campaign: CampaignCandidate, excluded_segments: list[ICPIdentity]
    ) -> ICPResult:
        """Return one candidate profile."""


async def research_icps(
    campaign: CampaignCandidate,
    *,
    config: ICPConfig | None = None,
    session: Session | None = None,
    agent: ICPResearcher | None = None,
    on_accepted: Callable[[ICPResult], None] | None = None,
) -> ICPBatchResult:
    """Research and transactionally persist distinct ICPs for a campaign."""
    effective_config = config or get_icp_config(load_settings())
    owns_session = session is None
    if session is None:
        session = make_session_factory(make_engine())()
    researchers = (
        [agent]
        if agent is not None
        else [
            ICPResearchAgent()
            for _ in range(min(effective_config.batch_size, effective_config.concurrency))
        ]
    )
    owns_agent = agent is None
    run_id = uuid4().hex
    accepted: list[ICPResult] = []
    rejections: list[ICPRejection] = []
    attempt = 0

    def exclusions() -> list[ICPIdentity]:
        rows = session.scalars(
            select(ICP)
            .where(ICP.campaign_id == campaign.campaign_id)
            .order_by(ICP.created_at)
            .limit(effective_config.max_exclusion_cards)
        )
        return [ICPResult.model_validate(row.payload).identity for row in rows]

    try:
        if owns_agent:
            for researcher in researchers:
                if hasattr(researcher, "event_manager"):
                    ConsoleProgress().attach(researcher)

        async def research_slot(researcher: ICPResearcher) -> None:
            nonlocal attempt
            for _ in range(effective_config.max_attempts_per_slot):
                attempt += 1
                candidate = await researcher.research_one(campaign, exclusions())
                if candidate.campaign_id != campaign.campaign_id:
                    rejections.append(
                        ICPRejection(
                            attempt=attempt,
                            reason="candidate campaign_id did not match the requested campaign",
                            icp_id=candidate.icp_id,
                            segment_key=candidate.identity.segment_key(),
                        )
                    )
                    continue
                try:
                    persist_icp(candidate, session)
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    rejections.append(
                        ICPRejection(
                            attempt=attempt,
                            reason="candidate identity or icp_id already exists for this campaign",
                            icp_id=candidate.icp_id,
                            segment_key=candidate.identity.segment_key(),
                        )
                    )
                    continue
                if on_accepted is not None:
                    on_accepted(candidate)
                accepted.append(candidate)
                return

        tasks = [
            asyncio.create_task(research_slot(researchers[index % len(researchers)]))
            for index in range(effective_config.batch_size)
        ]
        await asyncio.gather(*tasks)
        return ICPBatchResult(
            campaign_id=campaign.campaign_id,
            run_id=run_id,
            requested_count=effective_config.batch_size,
            icps=accepted,
            rejections=rejections,
            exhaustion_reason=None,
        )
    finally:
        if owns_agent:
            for researcher in researchers:
                if hasattr(researcher, "browser"):
                    await aclose_browser(researcher.browser)
        if owns_session:
            session.close()
