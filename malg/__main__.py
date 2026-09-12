"""Run the saved-campaign account research smoke entry point."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from malg.core.account_probes import AccountProbeService
from malg.core.agents.account_research import AccountResearchAgent
from malg.core.agents.account_validation import AccountValidationAgent
from malg.core.agents.campaign_research import CampaignResearchAgent
from malg.core.browser_support import aclose_browser
from malg.core.icp_runner import research_icps
from malg.core.models.account import (
    AccountCandidate,
    AccountIdentity,
    AccountProbeReport,
    AccountValidationAssessment,
)
from malg.core.models.campaign import CampaignCandidate
from malg.core.models.icp import ICPBatchResult, ICPResult
from malg.core.persistent_memory_support import close_persistent_memory
from malg.database.artifacts import persist_account_candidate
from malg.database.models import ICP, Account, Campaign
from malg.database.session import make_engine, make_session_factory
from malg.utils.console_progress import ConsoleProgress


def load_first_persisted_icp(
    session: Session,
) -> tuple[CampaignCandidate, ICPResult, list[AccountIdentity]]:
    """Load the first persisted ICP, its campaign, and known account identities."""
    icp_row = session.scalar(select(ICP).order_by(ICP.campaign_id, ICP.icp_id, ICP.id).limit(1))
    if icp_row is None:
        raise RuntimeError("No persisted ICPs are available for the account smoke run.")

    campaign_row = session.get(Campaign, icp_row.campaign_id)
    if campaign_row is None:
        raise RuntimeError("The persisted ICP has no campaign parent.")

    campaign = CampaignCandidate.model_validate(campaign_row.payload)
    icp = ICPResult.model_validate(icp_row.payload)
    accounts = session.scalars(select(Account).order_by(Account.identity_key))
    excluded_accounts = [
        AccountIdentity.model_validate(account.payload["identity"]) for account in accounts
    ]
    return campaign, icp, excluded_accounts


async def research_and_validate_one_account(
    campaign: CampaignCandidate,
    icp: ICPResult,
    excluded_accounts: list[AccountIdentity],
) -> tuple[AccountCandidate, AccountProbeReport, AccountValidationAssessment]:
    """Research, probe, and independently validate one account candidate."""
    researcher = AccountResearchAgent()
    try:
        candidate = await researcher.research_one(campaign, icp, excluded_accounts)
    finally:
        if hasattr(researcher, "browser"):
            await aclose_browser(researcher.browser)

    probes = await AccountProbeService().probe_candidate(candidate)

    validator = AccountValidationAgent()
    try:
        validation = await validator.validate_account(campaign, icp, candidate, probes)
    finally:
        if hasattr(validator, "browser"):
            await aclose_browser(validator.browser)
    return candidate, probes, validation


async def find_one_campaign() -> CampaignCandidate:
    """Research one Eurostat-backed campaign with scoped persistent memory."""
    agent = CampaignResearchAgent()
    try:
        if hasattr(agent, "event_manager"):
            ConsoleProgress().attach(agent)
        return await agent.find_campaign()
    finally:
        if hasattr(agent, "browser"):
            await aclose_browser(agent.browser)
        close_persistent_memory(agent)


async def find_ten_icps(
    campaign: CampaignCandidate,
    *,
    session: Session | None = None,
    on_accepted: Callable[[ICPResult], None] | None = None,
) -> ICPBatchResult:
    """Research the next configured ICP batch for a supplied durable campaign."""
    return await research_icps(campaign, session=session, on_accepted=on_accepted)

async def main() -> None:
    """Research, validate, and persist one account for the first stored ICP."""
    engine = make_engine()
    sessions = make_session_factory(engine)
    try:
        with sessions() as session:
            campaign, icp, excluded_accounts = load_first_persisted_icp(session)

        candidate, probes, validation = await research_and_validate_one_account(
            campaign, icp, excluded_accounts
        )
        with sessions.begin() as session:
            persist_account_candidate(candidate, validation, session)
    finally:
        engine.dispose()

    result = {
        "campaign": campaign.model_dump(mode="json"),
        "icp": icp.model_dump(mode="json"),
        "candidate": candidate.model_dump(mode="json"),
        "probes": probes.model_dump(mode="json"),
        "validation": validation.model_dump(mode="json"),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
