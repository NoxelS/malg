"""Claim-fenced, replay-safe Twenty publication with durable immutable intents."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from malg.config import TwentyConfig
from malg.core.models.account import AccountResearchResult, AccountValidationAssessment
from malg.core.models.campaign import CampaignData
from malg.core.models.icp import ICPData
from malg.crm.client import (
    TwentyClient,
    TwentyConflict,
    TwentyError,
    TwentyRecordMissing,
    TwentySchemaIncompatible,
    TwentyUnavailable,
)
from malg.crm.identity import deterministic_id, normalize_domain, normalize_linkedin
from malg.crm.matching import choose_company_match, choose_person_match
from malg.crm.schema import contract_hash, load_manifest
from malg.database.crm_writes import (
    CrmWriteConflict,
    mark_write,
    prepare_write,
    reconcile_write,
    record_mutation_attempt,
)
from malg.database.jobs import require_claim
from malg.database.models import CrmWriteOperation, ResearchJob


class CrmPublisher:
    """Mutate only under a live claim, never holding a database transaction over HTTP."""

    def __init__(
        self, session_factory: sessionmaker[Session], config: TwentyConfig, client: TwentyClient
    ) -> None:
        self._session_factory, self._config, self._client = session_factory, config, client
        self._manifest = load_manifest()
        self._contract_hash = contract_hash(self._manifest)
        self._contract_version = self._manifest["contract_version"]

    @property
    def workspace_id(self) -> str:
        """Return the configured workspace namespace for new deterministic IDs."""
        return self._config.workspace_id

    def _require_claim(self, job_id: str, claim_token: str) -> None:
        with self._session_factory.begin() as session:
            job = require_claim(session, job_id, claim_token, datetime.now(UTC))
            deadline = job.deadline_at
            if deadline is not None and (
                deadline.replace(tzinfo=UTC)
                if deadline.tzinfo is None
                else deadline.astimezone(UTC)
            ) <= datetime.now(UTC):
                raise TimeoutError("publication deadline exhausted")

    async def _check(self, job_id: str, claim_token: str) -> None:
        self._require_claim(job_id, claim_token)
        contract = await self._client.check_contract()
        self._require_claim(job_id, claim_token)
        if not contract["schema_compatible"]:
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        with self._session_factory.begin() as session:
            job = require_claim(session, job_id, claim_token, datetime.now(UTC))
            if (
                job.contract_version != contract["contract_version"]
                or job.contract_hash != contract["contract_hash"]
            ):
                raise TwentyConflict("crm_input_changed")

    def _prepare(
        self,
        job_id: str,
        stage_key: str,
        object_name: str,
        record_id: str,
        field: str,
        fields: Mapping[str, object],
        version: str | None,
        claim_token: str,
    ) -> CrmWriteOperation:
        with self._session_factory.begin() as session:
            job = require_claim(session, job_id, claim_token, datetime.now(UTC))
            return prepare_write(
                session,
                job_id=job_id,
                workflow_id=job.workflow_id,
                stage_key=stage_key,
                object_name=object_name,
                record_id=record_id,
                field_key=field,
                contract_version=self._contract_version,
                contract_hash=self._contract_hash,
                intended_fields=fields,
                observed_record_version=version,
                claim_token=claim_token,
            )

    def _mark(
        self, operation_id: str, status: str, claim_token: str, error: str | None = None
    ) -> None:
        with self._session_factory.begin() as session:
            operation = session.get(CrmWriteOperation, operation_id)
            if operation is None:
                raise ValueError("missing CRM intent")
            mark_write(session, operation, status=status, claim_token=claim_token, error=error)
            if status == "confirmed":
                self._attach_ref(session, operation)

    def _attempt(self, operation_id: str, claim_token: str) -> None:
        with self._session_factory.begin() as session:
            operation = session.get(CrmWriteOperation, operation_id)
            if operation is None:
                raise ValueError("missing CRM intent")
            record_mutation_attempt(session, operation, claim_token=claim_token)

    def _attach_ref(self, session: Session, operation: CrmWriteOperation) -> None:
        job = session.get(ResearchJob, operation.job_id)
        if job is None:
            return
        ref = {
            "object_name": operation.object_name,
            "record_id": operation.record_id,
            "url": self._config.public_url,
        }
        if not any(
            item.get("object_name") == operation.object_name
            and item.get("record_id") == operation.record_id
            for item in job.result_refs
        ):
            job.result_refs = [*job.result_refs, ref]

    async def _read(
        self, job_id: str, object_name: str, record_id: str, claim_token: str
    ) -> dict[str, Any] | None:
        self._require_claim(job_id, claim_token)
        try:
            remote = await self._client.read_record(object_name, record_id)
        except TwentyRecordMissing:
            remote = None
        self._require_claim(job_id, claim_token)
        return remote

    async def _publish(
        self,
        job_id: str,
        stage_key: str,
        object_name: str,
        record_id: str,
        fields: Mapping[str, object],
        *,
        claim_token: str,
    ) -> str:
        await self._check(job_id, claim_token)
        operation = self._prepare(
            job_id, stage_key, object_name, record_id, "projection", fields, None, claim_token
        )
        if operation.status in {"cancelled", "conflict"}:
            raise CrmWriteConflict("terminal CRM intent prohibits publication")
        for attempt in range(self._config.max_retries + 1):
            remote = await self._read(job_id, object_name, record_id, claim_token)
            if remote is not None:
                if not _identity_match(object_name, remote, fields):
                    if operation.status != "confirmed":
                        self._mark(
                            operation.operation_id, "conflict", claim_token, "crm_identity_conflict"
                        )
                    raise TwentyConflict("crm_identity_conflict")
                self._mark(operation.operation_id, "confirmed", claim_token)
                return record_id
            if operation.status == "confirmed":
                raise TwentyRecordMissing("crm_record_missing")
            self._require_claim(job_id, claim_token)
            self._attempt(operation.operation_id, claim_token)
            try:
                await self._client.create_record(object_name, record_id, fields)
            except TwentyError as error:
                # Read after every ambiguous/duplicate failure before considering retry.
                remote = await self._read(job_id, object_name, record_id, claim_token)
                if remote is not None:
                    if _identity_match(object_name, remote, fields):
                        self._mark(operation.operation_id, "confirmed", claim_token)
                        return record_id
                    self._mark(
                        operation.operation_id, "conflict", claim_token, "crm_identity_conflict"
                    )
                    raise TwentyConflict("crm_identity_conflict") from error
                if not isinstance(error, TwentyUnavailable):
                    self._mark(operation.operation_id, "failed", claim_token, "crm_write_rejected")
                    raise
                if attempt >= self._config.max_retries:
                    raise
                self._require_claim(job_id, claim_token)
                await asyncio.sleep(
                    error.retry_after if error.retry_after is not None else min(2**attempt, 4)
                )
                continue
            self._require_claim(job_id, claim_token)
            self._mark(operation.operation_id, "confirmed", claim_token)
            return record_id
        raise TwentyUnavailable("crm_unavailable")

    async def publish_campaign(self, job_id: str, data: CampaignData, *, claim_token: str) -> str:
        """Create one job-identified Campaign; replay never updates human-edited fields."""
        record_id = str(deterministic_id(self.workspace_id, "malgCampaign", str(UUID(job_id))))
        return await self._publish(
            job_id,
            "campaign.publication",
            "malgCampaign",
            record_id,
            data.model_dump(),
            claim_token=claim_token,
        )

    async def publish_icp(
        self, job_id: str, data: ICPData, *, campaign_id: str, claim_token: str
    ) -> str:
        """Use real parent and normalized targeting, excluding generated display title."""
        identity = {
            key: value.strip().casefold() if isinstance(value, str) else value
            for key, value in data.model_dump(exclude={"name"}).items()
        }
        identity["campaign_id"] = str(UUID(campaign_id))
        record_id = str(
            deterministic_id(
                self.workspace_id,
                "malgIcp",
                json.dumps(identity, sort_keys=True, separators=(",", ":")),
            )
        )
        fields = {
            "name": data.name,
            "sector": data.sector,
            "geography": data.geography,
            "employeesMin": data.employees_min,
            "employeesMax": data.employees_max,
            "buyerRole": data.buyer_role,
            "workflow": data.workflow,
            "campaignId": campaign_id,
        }
        return await self._publish(
            job_id, "icp.publication", "malgIcp", record_id, fields, claim_token=claim_token
        )

    async def match_company(
        self, job_id: str, result: AccountResearchResult, *, claim_token: str
    ) -> tuple[str | None, str | None]:
        """Require independent observed identities to agree on at most one Company."""
        domain = _company_domain(result)
        if domain is None:
            return None, "crm_identity_conflict"
        linkedin = (
            str(result.data.linkedin_url) if result.data and result.data.linkedin_url else None
        )
        self._require_claim(job_id, claim_token)
        domains = await self._client.find_companies(domain_url=domain, linkedin_url=None)
        self._require_claim(job_id, claim_token)
        links = (
            await self._client.find_companies(domain_url=None, linkedin_url=linkedin)
            if linkedin
            else []
        )
        self._require_claim(job_id, claim_token)
        identifier, reason = choose_company_match(domains, links)
        if identifier and not reason:
            remote = next(row for row in [*domains, *links] if row["id"] == identifier)
            if not _identity_match(
                "company",
                remote,
                {
                    "domainName": {"primaryLinkUrl": domain},
                    "linkedinLink": {"primaryLinkUrl": linkedin},
                },
            ):
                return None, "crm_identity_conflict"
        return identifier, reason

    async def publish_account(
        self,
        job_id: str,
        result: AccountResearchResult,
        validation: AccountValidationAssessment,
        *,
        icp_id: str,
        claim_token: str,
    ) -> tuple[str | None, str | None, str | None]:
        """Publish only independently accepted Company and Membership; retain partial effects."""
        if (
            result.qualification.value != "accepted"
            or validation.outcome.value != "accepted"
            or result.data is None
        ):
            return None, None, "account_not_qualified"
        await self._check(job_id, claim_token)
        company_id, reason = await self.match_company(job_id, result, claim_token=claim_token)
        if reason:
            return None, None, reason
        domain = _company_domain(result)
        if domain is None:
            return None, None, "crm_identity_conflict"
        data = result.data
        company_id = company_id or str(deterministic_id(self.workspace_id, "company", domain))
        fields: dict[str, object] = {"name": data.name, "domainName": {"primaryLinkUrl": domain}}
        if data.sector is not None:
            fields["malgSector"] = data.sector
        if data.employees is not None:
            fields["malgEmployees"] = data.employees
        if data.linkedin_url is not None:
            fields["linkedinLink"] = {"primaryLinkUrl": str(data.linkedin_url)}
        if data.annual_revenue is not None:
            micros = data.annual_revenue.amount * 1_000_000
            if micros != micros.to_integral_value() or micros > 9_007_199_254_740_991:
                raise ValueError("currency cannot be represented as exact micros")
            fields["annualRevenue"] = {
                "amountMicros": str(int(micros)),
                "currencyCode": data.annual_revenue.currency_code,
            }
        await self._publish(
            job_id, "account.company", "company", company_id, fields, claim_token=claim_token
        )
        membership_id = str(
            deterministic_id(self.workspace_id, "malgMembership", f"{company_id}:{icp_id}")
        )
        await self._publish(
            job_id,
            "account.membership",
            "malgMembership",
            membership_id,
            {"name": f"{data.name} — {icp_id}", "companyId": company_id, "icpId": icp_id},
            claim_token=claim_token,
        )
        return company_id, membership_id, None

    async def match_person(
        self,
        job_id: str,
        *,
        company_id: str,
        linkedin_url: str | None = None,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        claim_token: str,
    ) -> tuple[str | None, str | None]:
        """Match observed identities independently and never reparent an existing Person."""
        groups: list[list[dict[str, Any]]] = []
        for query in (
            {"linkedin_url": linkedin_url} if linkedin_url else {},
            {"email": email} if email else {},
            {"first_name": first_name, "last_name": last_name} if first_name else {},
        ):
            self._require_claim(job_id, claim_token)
            groups.append(
                await self._client.find_people(company_id=company_id, **query) if query else []
            )
        self._require_claim(job_id, claim_token)
        identifier, reason = choose_person_match(*groups)
        if identifier:
            remote = next(row for group in groups for row in group if row["id"] == identifier)
            fields = {
                "companyId": company_id,
                "emails": {"primaryEmail": email},
                "linkedinLink": {"primaryLinkUrl": linkedin_url},
                "name": {"firstName": first_name or "", "lastName": last_name or ""},
            }
            if not _identity_match("person", remote, fields):
                return None, "crm_identity_conflict"
        return identifier, reason

    async def publish_person(
        self,
        job_id: str,
        person_id: str,
        company_id: str,
        fields: Mapping[str, object],
        *,
        claim_token: str,
    ) -> str:
        """Create a native Person linked to its host-validated Company."""
        return await self._publish(
            job_id,
            "person.publication",
            "person",
            person_id,
            {**fields, "companyId": company_id},
            claim_token=claim_token,
        )

    async def hydrate(
        self,
        job_id: str,
        object_name: str,
        record_id: str,
        observed_version: str,
        proposals: Mapping[str, object],
        *,
        claim_token: str,
    ) -> list[dict[str, str | None]]:
        """Journal one guarded field mutation at a time and reconcile zero-row races."""
        await self._check(job_id, claim_token)
        outcomes = []
        for field, value in proposals.items():
            operation = self._prepare(
                job_id,
                f"{object_name}.hydration",
                object_name,
                record_id,
                field,
                {field: value},
                observed_version,
                claim_token,
            )
            if operation.status in {"confirmed", "conflict", "cancelled"}:
                outcomes.append(
                    {
                        "field": field,
                        "status": operation.status,
                        "reason": operation.sanitized_error,
                    }
                )
                continue
            version = observed_version
            for attempt in range(self._config.max_retries + 1):
                current = await self._read(job_id, object_name, record_id, claim_token)
                if current is None:
                    raise TwentyRecordMissing("crm_record_missing")
                if _contains_value(current.get(field), value):
                    self._mark(operation.operation_id, "confirmed", claim_token)
                    break
                if not _empty_value(field, current.get(field), value):
                    self._mark(operation.operation_id, "conflict", claim_token, "crm_input_changed")
                    break
                version = str(current["updatedAt"])
                self._require_claim(job_id, claim_token)
                self._attempt(operation.operation_id, claim_token)
                try:
                    await self._client.fill_missing(object_name, record_id, version, {field: value})
                except (TwentyConflict, TwentyUnavailable) as error:
                    # The next loop re-reads identity/version before any additional mutation.
                    if attempt == self._config.max_retries:
                        final = await self._read(job_id, object_name, record_id, claim_token)
                        if final is not None and _contains_value(final.get(field), value):
                            self._mark(operation.operation_id, "confirmed", claim_token)
                        elif final is not None and not _empty_value(field, final.get(field), value):
                            self._mark(
                                operation.operation_id, "conflict", claim_token, "crm_input_changed"
                            )
                    elif isinstance(error, TwentyUnavailable):
                        self._require_claim(job_id, claim_token)
                        await asyncio.sleep(
                            error.retry_after
                            if error.retry_after is not None
                            else min(2**attempt, 4)
                        )
                    continue
                except TwentyError:
                    self._mark(operation.operation_id, "failed", claim_token, "crm_write_rejected")
                    raise
                self._require_claim(job_id, claim_token)
                self._mark(operation.operation_id, "confirmed", claim_token)
                break
            with self._session_factory() as session:
                stored = session.get(CrmWriteOperation, operation.operation_id)
                if stored is None:
                    raise ValueError("missing CRM intent")
                outcomes.append(
                    {"field": field, "status": stored.status, "reason": stored.sanitized_error}
                )
        return outcomes

    async def reconcile_pending(self, job_id: str | None = None) -> None:
        """Observe unresolved effects without sending mutations or reopening any job."""
        with self._session_factory() as session:
            query = select(CrmWriteOperation).where(
                CrmWriteOperation.status.in_(("prepared", "failed"))
            )
            if job_id is not None:
                query = query.where(CrmWriteOperation.job_id == job_id)
            else:
                query = query.join(
                    ResearchJob, ResearchJob.job_id == CrmWriteOperation.job_id
                ).where(
                    or_(
                        ResearchJob.status.in_(("succeeded", "failed", "cancelled")),
                        (ResearchJob.status == "running")
                        & (ResearchJob.claim_expires_at <= datetime.now(UTC)),
                    )
                )
            operations = list(session.scalars(query))
        for operation in operations:
            try:
                remote = await self._client.read_record(operation.object_name, operation.record_id)
            except TwentyRecordMissing:
                if operation.attempt_count == 0 and job_id is None:
                    with self._session_factory.begin() as session:
                        stored = session.get(CrmWriteOperation, operation.operation_id)
                        if stored is not None:
                            reconcile_write(session, stored, status="cancelled")
                continue
            valid = (
                _identity_match(operation.object_name, remote, operation.intended_fields)
                if operation.field_key == "projection"
                else _contains_value(
                    remote.get(operation.field_key), operation.intended_fields[operation.field_key]
                )
            )
            if (
                not valid
                and operation.field_key != "projection"
                and _empty_value(
                    operation.field_key,
                    remote.get(operation.field_key),
                    operation.intended_fields[operation.field_key],
                )
            ):
                continue
            with self._session_factory.begin() as session:
                stored = session.get(CrmWriteOperation, operation.operation_id)
                if stored is None:
                    continue
                reconcile_write(
                    session,
                    stored,
                    status="confirmed" if valid else "conflict",
                    error=None if valid else "crm_identity_conflict",
                )
                if valid:
                    self._attach_ref(session, stored)


def _contains_value(actual: object, intended: object) -> bool:
    if isinstance(intended, Mapping):
        if not isinstance(actual, Mapping):
            return False
        for key, value in intended.items():
            if key == "amountMicros":
                try:
                    if Decimal(str(actual.get(key))) != Decimal(str(value)):
                        return False
                except InvalidOperation:
                    return False
            elif not _contains_value(actual.get(key), value):
                return False
        return True
    return actual == intended


def _empty_value(field: str, actual: object, intended: object) -> bool:
    if field == "name" and isinstance(actual, Mapping) and isinstance(intended, Mapping):
        return set(intended) == {"lastName"} and actual.get("lastName") in (None, "")
    if isinstance(actual, Mapping):
        return all(value in (None, "", [], {}) for value in actual.values())
    return actual in (None, "")


def _identity_match(
    object_name: str, remote: Mapping[str, Any], intended: Mapping[str, object]
) -> bool:
    """Compare observed identity/parents, never mutable names or firmographics on replay."""
    for key in ("campaignId", "companyId", "icpId"):
        if key in intended and remote.get(key) != intended[key]:
            return False
    anchored = False
    for field, component, secondary in (
        ("domainName", "primaryLinkUrl", "secondaryLinks"),
        ("linkedinLink", "primaryLinkUrl", "secondaryLinks"),
        ("emails", "primaryEmail", "additionalEmails"),
    ):
        proposed, observed = intended.get(field), remote.get(field)
        if not isinstance(proposed, Mapping) or not proposed.get(component):
            continue
        wanted = str(proposed[component])
        if not isinstance(observed, Mapping):
            continue
        raw_values = [observed.get(component)]
        raw_values.extend(
            item.get("url") if isinstance(item, Mapping) else item
            for item in observed.get(secondary, []) or []
        )
        actuals = [str(value) for value in raw_values if value]
        if not actuals:
            continue
        try:
            if field == "domainName":
                equal = normalize_domain(wanted) in {normalize_domain(value) for value in actuals}
            elif field == "linkedinLink":
                equal = normalize_linkedin(wanted, person=object_name == "person") in {
                    normalize_linkedin(value, person=object_name == "person") for value in actuals
                }
            else:
                equal = wanted.casefold() in {value.casefold() for value in actuals}
        except ValueError:
            return False
        if not equal:
            return False
        anchored = True
    if object_name == "person" and not anchored:
        actual_name, proposed_name = remote.get("name"), intended.get("name")
        if isinstance(actual_name, Mapping) and isinstance(proposed_name, Mapping):
            actual = " ".join(
                str(actual_name.get(part) or "").strip().casefold()
                for part in ("firstName", "lastName")
            ).strip()
            wanted = " ".join(
                str(proposed_name.get(part) or "").strip().casefold()
                for part in ("firstName", "lastName")
            ).strip()
            anchored = bool(wanted) and actual == wanted
    return anchored if object_name in {"company", "person"} else True


def _company_domain(result: AccountResearchResult) -> str | None:
    if result.identity is None:
        return None
    values = list(result.identity.official_domains)
    if result.identity.official_website:
        values.append(str(result.identity.official_website))
    if result.data and result.data.website:
        values.append(str(result.data.website))
    domains = {normalize_domain(value) for value in values}
    return next(iter(domains)) if len(domains) == 1 else None
