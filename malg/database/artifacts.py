"""Persistence operations used by non-HTTP MALG entry points.

The command-line research run remains responsible for agent orchestration. This
module only stores already-validated artifacts using the same canonical schema
as the API.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from malg.core.models.account import (
    AccountCandidate,
    AccountValidationAssessment,
    CommunicationEndpointCandidate,
    ContactCandidate,
    EndpointKind,
)
from malg.core.models.campaign import CampaignCandidate
from malg.core.models.icp import ICPResult
from malg.database.models import (
    ICP,
    Account,
    AccountMatch,
    AccountValidationRun,
    Campaign,
    CommunicationEndpoint,
    Contact,
    Employment,
)


def persist_campaign(candidate: CampaignCandidate, session: Session) -> Campaign:
    """Persist one canonical campaign and reject duplicate identity."""
    campaign = Campaign(
        campaign_id=candidate.campaign_id,
        title=candidate.title,
        payload=candidate.model_dump(mode="json"),
    )
    session.add(campaign)
    session.flush()
    return campaign


def persist_icp(candidate: ICPResult, session: Session) -> ICP:
    """Persist one ICP beneath its existing campaign."""
    if session.get(Campaign, candidate.campaign_id) is None:
        raise ValueError("ICP candidate campaign does not exist.")
    icp = ICP(
        campaign_id=candidate.campaign_id,
        icp_id=candidate.icp_id,
        segment_key=candidate.identity.segment_key(),
        title=candidate.title,
        payload=candidate.model_dump(mode="json"),
    )
    session.add(icp)
    session.flush()
    return icp


def persist_icps(
    campaign: CampaignCandidate,
    icps: Iterable[ICPResult],
    session_factory: sessionmaker[Session],
) -> None:
    """Store validated ICPs from a run in one transaction."""
    icp_list = list(icps)
    if any(icp.campaign_id != campaign.campaign_id for icp in icp_list):
        raise ValueError("Every persisted ICP must belong to the saved campaign.")
    with session_factory.begin() as session:
        if session.get(Campaign, campaign.campaign_id) is None:
            persist_campaign(campaign, session)
        for icp in icp_list:
            persist_icp(icp, session)


def _normalized_text(value: str) -> str:
    """Return a compact comparison key without changing the stored source value."""
    return " ".join(value.casefold().split())


def _primary_domain(candidate: AccountCandidate) -> str | None:
    """Return the first declared official domain, or the official website host."""
    if candidate.identity.official_domains:
        return candidate.identity.official_domains[0].casefold()
    if candidate.identity.official_website is None:
        return None
    return urlparse(str(candidate.identity.official_website)).hostname


def account_identity_key(candidate: AccountCandidate) -> str:
    """Derive the stable host-owned account deduplication key.

    Registry identity is stronger than a domain. A website is required by the
    candidate contract when a registry identifier is unavailable.
    """
    identity = candidate.identity
    if identity.registry_jurisdiction and identity.registration_number:
        return "registry:" + _normalized_text(
            f"{identity.registry_jurisdiction}:{identity.registration_number}"
        )
    domain = _primary_domain(candidate)
    if domain is None:
        raise ValueError("Account candidate has no identity key anchor.")
    return f"domain:{domain.casefold()}"


def _contact_identity_key(account_id: str, contact: ContactCandidate) -> str:
    """Prefer an observed LinkedIn URL; otherwise scope a name to the account."""
    linkedin = next(
        (
            endpoint.value
            for endpoint in contact.endpoints
            if endpoint.kind is EndpointKind.LINKEDIN
        ),
        None,
    )
    if linkedin is not None:
        return f"linkedin:{linkedin.strip().rstrip('/').casefold()}"
    return f"account:{account_id}:{_normalized_text(contact.full_name)}"


def _normalize_endpoint(endpoint: CommunicationEndpointCandidate) -> str:
    """Normalize a comparison key while preserving the original endpoint value."""
    value = endpoint.value.strip()
    if endpoint.kind is EndpointKind.EMAIL and "@" in value:
        local_part, domain = value.rsplit("@", maxsplit=1)
        return f"{local_part}@{domain.casefold()}"
    return value.rstrip("/").casefold()


def _upsert_endpoint(
    session: Session,
    endpoint: CommunicationEndpointCandidate,
    *,
    account_id: str | None = None,
    contact_id: str | None = None,
) -> None:
    """Store one endpoint under exactly one owner, updating refreshed evidence."""
    if (account_id is None) == (contact_id is None):
        raise ValueError("An endpoint must belong to exactly one account or contact.")
    owner_key = f"account:{account_id}" if account_id is not None else f"contact:{contact_id}"
    normalized_value = _normalize_endpoint(endpoint)
    stored = session.scalar(
        select(CommunicationEndpoint).where(
            CommunicationEndpoint.owner_key == owner_key,
            CommunicationEndpoint.kind == endpoint.kind.value,
            CommunicationEndpoint.normalized_value == normalized_value,
        )
    )
    if stored is None:
        session.add(
            CommunicationEndpoint(
                endpoint_id=str(uuid4()),
                account_id=account_id,
                contact_id=contact_id,
                owner_key=owner_key,
                kind=endpoint.kind.value,
                value=endpoint.value,
                normalized_value=normalized_value,
                discovery_method=endpoint.discovery_method.value,
                payload=endpoint.model_dump(mode="json"),
            )
        )
        return
    stored.value = endpoint.value
    stored.discovery_method = endpoint.discovery_method.value
    stored.payload = endpoint.model_dump(mode="json")


def _persist_contact(session: Session, account_id: str, contact: ContactCandidate) -> None:
    """Upsert a person, current employment, and their sourced endpoints."""
    identity_key = _contact_identity_key(account_id, contact)
    stored_contact = session.scalar(select(Contact).where(Contact.identity_key == identity_key))
    if stored_contact is None:
        stored_contact = Contact(
            contact_id=str(uuid4()),
            identity_key=identity_key,
            full_name=contact.full_name,
            payload=contact.model_dump(mode="json"),
        )
        session.add(stored_contact)
        session.flush()
    else:
        stored_contact.full_name = contact.full_name
        stored_contact.payload = contact.model_dump(mode="json")

    employment = session.scalar(
        select(Employment).where(
            Employment.account_id == account_id,
            Employment.contact_id == stored_contact.contact_id,
        )
    )
    if employment is None:
        session.add(
            Employment(
                employment_id=str(uuid4()),
                account_id=account_id,
                contact_id=stored_contact.contact_id,
                title=contact.title,
                buyer_role=contact.buyer_role,
                payload=contact.model_dump(mode="json"),
            )
        )
    else:
        employment.title = contact.title
        employment.buyer_role = contact.buyer_role
        employment.payload = contact.model_dump(mode="json")

    for endpoint in contact.endpoints:
        _upsert_endpoint(session, endpoint, contact_id=stored_contact.contact_id)


def persist_account_candidate(
    candidate: AccountCandidate,
    validation: AccountValidationAssessment | None,
    session: Session,
) -> AccountMatch:
    """Persist one candidate, its current contacts, and optional validation assessment.

    The caller owns the surrounding transaction. This function does not run
    agents or probes and rejects candidates whose campaign/ICP parents are
    absent or inconsistent.
    """
    campaign = session.get(Campaign, candidate.campaign_id)
    if campaign is None:
        raise ValueError("Account candidate campaign does not exist.")
    icp = session.scalar(
        select(ICP).where(ICP.campaign_id == candidate.campaign_id, ICP.icp_id == candidate.icp_id)
    )
    if icp is None:
        raise ValueError("Account candidate ICP does not exist in its campaign.")

    identity_key = account_identity_key(candidate)
    account = session.scalar(select(Account).where(Account.identity_key == identity_key))
    account_payload = {
        "identity": candidate.identity.model_dump(mode="json"),
        "firmographics": candidate.firmographics.model_dump(mode="json"),
        "operating_profile": candidate.operating_profile.model_dump(mode="json"),
    }
    if account is None:
        account = Account(
            account_id=str(uuid4()),
            identity_key=identity_key,
            display_name=candidate.identity.display_name,
            primary_domain=_primary_domain(candidate),
            payload=account_payload,
        )
        session.add(account)
        session.flush()
    else:
        account.display_name = candidate.identity.display_name
        account.primary_domain = _primary_domain(candidate)
        account.payload = account_payload

    match = session.scalar(
        select(AccountMatch).where(
            AccountMatch.campaign_id == candidate.campaign_id,
            AccountMatch.icp_id == candidate.icp_id,
            AccountMatch.account_id == account.account_id,
        )
    )
    if match is None:
        match = AccountMatch(
            account_match_id=str(uuid4()),
            campaign_id=candidate.campaign_id,
            icp_id=candidate.icp_id,
            account_id=account.account_id,
            fit_score=candidate.fit.score,
            status=(validation.outcome.value if validation is not None else "needs_review"),
            payload=candidate.model_dump(mode="json"),
        )
        session.add(match)
        session.flush()
    else:
        match.fit_score = candidate.fit.score
        match.payload = candidate.model_dump(mode="json")
        if validation is not None:
            match.status = validation.outcome.value

    for endpoint in candidate.account_endpoints:
        _upsert_endpoint(session, endpoint, account_id=account.account_id)
    for contact in candidate.contacts:
        _persist_contact(session, account.account_id, contact)

    if validation is not None:
        session.add(
            AccountValidationRun(
                validation_run_id=str(uuid4()),
                account_match_id=match.account_match_id,
                outcome=validation.outcome.value,
                payload=validation.model_dump(mode="json"),
            )
        )
    return match
