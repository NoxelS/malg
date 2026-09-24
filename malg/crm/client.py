"""Small asynchronous GraphQL client for the managed Twenty data plane."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel

from malg.config import TwentyConfig
from malg.core.models.account import AccountData, Money
from malg.core.models.campaign import CampaignData
from malg.core.models.icp import ICPData
from malg.core.models.person import PersonData
from malg.crm.contracts import CRMPage, CRMRecord
from malg.crm.identity import normalize_domain, normalize_linkedin

# Keep micros exact across the installed BigFloat JavaScript resolver.
_MAX_CURRENCY_MICROS = 9_007_199_254_740_991


def _retry_after(value: str | None) -> float | None:
    """Bound server retry guidance without accepting malformed or negative delays."""
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            delay = (parsed - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return min(max(delay, 0), 4) if delay == delay else None


_RECORD_OPERATIONS = {
    "company": ("Company", "CompanyCreateInput"),
    "person": ("Person", "PersonCreateInput"),
    "malgCampaign": ("MalgCampaign", "MalgCampaignCreateInput"),
    "malgIcp": ("MalgIcp", "MalgIcpCreateInput"),
    "malgMembership": ("MalgMembership", "MalgMembershipCreateInput"),
}

_MISSING_FIELD_OPERATIONS = {
    "company": (
        "updateCompanies",
        "CompanyUpdateInput",
        "CompanyFilterInput",
        frozenset({"malgEmployees", "malgSector", "domainName", "annualRevenue", "linkedinLink"}),
    ),
    "person": (
        "updatePeople",
        "PersonUpdateInput",
        "PersonFilterInput",
        frozenset({"name", "jobTitle", "emails", "linkedinLink"}),
    ),
}
_CONNECTION_FILTER_TYPES = {
    "malgCampaigns": "MalgCampaignFilterInput",
    "malgIcps": "MalgIcpFilterInput",
    "companies": "CompanyFilterInput",
    "people": "PersonFilterInput",
}

_COMPANY_IDENTITY_QUERY = """
query Companies($filter: CompanyFilterInput!) {
  companies(first: 50, filter: $filter, orderBy: [{ id: AscNullsFirst }]) {
    edges {
      node {
        id
        updatedAt
        name
        domainName { primaryLinkUrl secondaryLinks { label url } }
        linkedinLink { primaryLinkUrl secondaryLinks { label url } }
        malgSector
        malgEmployees
      }
    }
    pageInfo { hasNextPage }
  }
}
"""


class TwentyError(RuntimeError):
    """Base class for sanitized Twenty failures."""


class TwentyUnavailable(TwentyError):
    """Twenty could not be reached after bounded retries."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TwentyClient:
    """Read strict native projections and issue bounded, explicit GraphQL writes.

    Every request uses the configured origin and credential, including with an
    injected transport. Only owned transports are closed. Missing records raise
    TwentyRecordMissing; malformed projections raise TwentySchemaIncompatible;
    unavailable/auth failures are sanitized TwentyError subclasses.
    """

    def __init__(self, config: TwentyConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._http = http_client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
        )
        self._owns_http = http_client is None

    async def aclose(self) -> None:
        """Close the owned transport; injected transports remain caller-owned."""
        if self._owns_http:
            await self._http.aclose()

    async def graphql(
        self, query: str, variables: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute a variable-bound record API operation."""
        return await self._graphql("/graphql", query, variables)

    async def metadata_graphql(
        self, query: str, variables: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute workspace metadata with the configured credential scope."""
        return await self._graphql("/metadata", query, variables)

    async def check_contract(self) -> dict[str, Any]:
        """Read-only compatibility gate using the runtime credential."""
        from malg.crm.schema import check_contract

        return await check_contract(self)

    async def read_scope(self, request: Mapping[str, Any]) -> dict[str, CRMRecord[Any]]:
        """Resolve actual parents and membership, never deriving IDs from request IDs."""
        records: dict[str, CRMRecord[Any]] = {}
        for scope, getter in (
            ("campaign", self.get_campaign),
            ("icp", self.get_icp),
            ("account", self.get_account),
            ("person", self.get_person),
        ):
            record_id = request.get(f"{scope}_id")
            if record_id is not None:
                records[scope] = await getter(UUID(str(record_id)))
        if "icp" in records:
            campaign_id = records["icp"].campaign_id
            if campaign_id is None:
                raise TwentySchemaIncompatible("crm_schema_incompatible")
            if "campaign" in records and records["campaign"].id != campaign_id:
                raise TwentyConflict("crm_scope_conflict")
            if "campaign" not in records:
                records["campaign"] = await self.get_campaign(campaign_id)
        if "person" in records and records["person"].company_id is not None:
            records["account"] = await self.get_account(records["person"].company_id)
        if request.get("kind") == "person" and not await self.has_membership(
            records["account"].id, records["icp"].id
        ):
            raise TwentyConflict("crm_scope_conflict")
        return records

    async def _graphql(
        self, endpoint: str, query: str, variables: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute GraphQL, retrying reads only and never replaying mutations."""
        payload = {"query": query, "variables": dict(variables or {})}
        is_mutation = bool(re.match(r"\s*mutation\b", query))
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await self._http.post(
                    f"{self.config.base_url.rstrip('/')}{endpoint}",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                    timeout=self.config.timeout_seconds,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if is_mutation or attempt >= self.config.max_retries:
                    raise TwentyUnavailable("Twenty transport unavailable") from error
                await asyncio.sleep(min(2**attempt, 4))
                continue
            retryable = response.status_code in {408, 429} or response.status_code >= 500
            if retryable and (not is_mutation) and attempt < self.config.max_retries:
                delay = _retry_after(response.headers.get("Retry-After"))
                await asyncio.sleep(delay if delay is not None else min(2**attempt, 4))
                continue
            if response.status_code >= 500 or response.status_code in {408, 429}:
                raise TwentyUnavailable(
                    "Twenty transport unavailable",
                    retry_after=_retry_after(response.headers.get("Retry-After")),
                )
            if response.status_code in {401, 403}:
                raise TwentyError("Twenty authentication or authorization failed")
            if response.status_code >= 400:
                raise TwentyError("Twenty rejected the request")
            try:
                decoded = response.json()
            except ValueError as error:
                raise TwentySchemaIncompatible("crm_schema_incompatible") from error
            if not isinstance(decoded, dict):
                raise TwentySchemaIncompatible("crm_schema_incompatible")
            if decoded.get("errors"):
                raise TwentyError("Twenty rejected the GraphQL operation")
            data = decoded.get("data")
            if not isinstance(data, dict):
                raise TwentySchemaIncompatible("crm_schema_incompatible")
            return data
        raise TwentyUnavailable("Twenty transport unavailable")

    async def create_record(
        self, object_name: str, record_id: str, fields: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Create one allowlisted Twenty record without updating an existing row."""
        if "id" in fields:
            raise ValueError("record identity is host-owned")
        record_id = str(UUID(record_id))
        operation_name, input_name = _record_operation(object_name)
        mutation = f"""
        mutation Create($data: {input_name}!) {{
          create{operation_name}(data: $data) {{ id updatedAt }}
        }}
        """
        data = await self.graphql(mutation, {"data": {"id": record_id, **fields}})
        record = data.get(f"create{operation_name}")
        if not isinstance(record, dict) or record.get("id") != record_id:
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        try:
            _parse_datetime(record.get("updatedAt"))
        except ValueError as error:
            raise TwentySchemaIncompatible("crm_schema_incompatible") from error
        return record

    async def read_record(self, object_name: str, record_id: str) -> dict[str, Any]:
        """Read native identity and mutable fields for read-only intent reconciliation."""
        specs = {
            "malgCampaign": ("malgCampaigns", "MalgCampaignFilterInput", _projection("campaign")),
            "malgIcp": ("malgIcps", "MalgIcpFilterInput", _projection("icp") + " campaignId"),
            "company": ("companies", "CompanyFilterInput", _projection("account")),
            "person": ("people", "PersonFilterInput", _projection("person") + " companyId"),
            "malgMembership": (
                "malgMemberships",
                "MalgMembershipFilterInput",
                "name companyId icpId",
            ),
        }
        if object_name not in specs:
            raise ValueError("unsupported CRM object")
        connection, filter_type, projection = specs[object_name]
        data = await self.graphql(
            f"""query Reconcile($filter: {filter_type}!) {{
                {connection}(first: 2, filter: $filter) {{
                    edges {{ node {{ id updatedAt deletedAt {projection} }} }}
                }}
            }}""",
            {"filter": {"id": {"eq": str(UUID(record_id))}}},
        )
        connection_data = data.get(connection)
        edges = connection_data.get("edges") if isinstance(connection_data, Mapping) else None
        if not isinstance(edges, list) or len(edges) > 1:
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        if not edges:
            raise TwentyRecordMissing("crm_record_missing")
        node = edges[0].get("node") if isinstance(edges[0], Mapping) else None
        if not isinstance(node, dict) or node.get("id") != record_id:
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        if node.get("deletedAt") is not None:
            raise TwentyRecordMissing("crm_record_missing")
        try:
            _parse_datetime(node.get("updatedAt"))
            _observed_composites(node)
        except (ValueError, TypeError) as error:
            raise TwentySchemaIncompatible("crm_schema_incompatible") from error
        return node

    async def find_people(
        self,
        *,
        company_id: str,
        linkedin_url: str | None = None,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Match observed Person identity globally; name fallback stays Company-scoped."""
        if linkedin_url:
            normalized_linkedin = normalize_linkedin(linkedin_url, person=True)
            record_filter: dict[str, Any] = {
                "or": [
                    {
                        "linkedinLink": {
                            "primaryLinkUrl": {
                                "ilike": f"%{normalized_linkedin.rstrip('/').rsplit('/', 1)[-1]}%"
                            }
                        }
                    },
                    _nonempty_native_array("linkedinLink", "secondaryLinks"),
                ]
            }
        elif email:
            email = email.strip().casefold()
            record_filter = {
                "or": [
                    {"emails": {"primaryEmail": {"ilike": email}}},
                    _nonempty_native_array("emails", "additionalEmails"),
                ]
            }
        elif first_name:
            record_filter = {"companyId": {"eq": company_id}}
        else:
            return []
        expected_name = " ".join(f"{first_name or ''} {last_name or ''}".split()).casefold()
        data = await self.graphql(
            f"""query People($filter: PersonFilterInput!) {{
                people(first: 50, filter: $filter, orderBy: [{{id: AscNullsFirst}}]) {{
                    edges {{ node {{ id updatedAt companyId {_projection("person")} }} }}
                    pageInfo {{ hasNextPage }}
                }}
            }}""",
            {"filter": record_filter},
        )
        connection = data.get("people")
        if not isinstance(connection, Mapping):
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        page_info, edges = connection.get("pageInfo"), connection.get("edges")
        if (
            not isinstance(page_info, Mapping)
            or not isinstance(page_info.get("hasNextPage"), bool)
            or not isinstance(edges, list)
        ):
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        if page_info["hasNextPage"]:
            raise TwentyConflict("crm_identity_conflict")
        records = []
        for edge in edges:
            node = edge.get("node") if isinstance(edge, Mapping) else None
            if not isinstance(node, dict) or not isinstance(node.get("id"), str):
                raise TwentySchemaIncompatible("crm_schema_incompatible")
            try:
                UUID(node["id"])
                _parse_datetime(node.get("updatedAt"))
                _observed_composites(node)
                if node.get("companyId") is not None:
                    UUID(node["companyId"])
            except (ValueError, TypeError, AttributeError) as error:
                raise TwentySchemaIncompatible("crm_schema_incompatible") from error
            if linkedin_url:
                links = node.get("linkedinLink")
                if not _has_link_identity(links, normalized_linkedin, person=True):
                    continue
            elif email:
                emails = node.get("emails")
                if not isinstance(emails, Mapping):
                    raise TwentySchemaIncompatible("crm_schema_incompatible")
                candidates = [emails.get("primaryEmail"), *(emails.get("additionalEmails") or [])]
                if email not in {
                    value.strip().casefold() for value in candidates if isinstance(value, str)
                }:
                    continue
            else:
                name = node.get("name")
                if not isinstance(name, Mapping):
                    raise TwentySchemaIncompatible("crm_schema_incompatible")
                actual_name = " ".join(
                    f"{name.get('firstName') or ''} {name.get('lastName') or ''}".split()
                ).casefold()
                if node.get("companyId") != company_id or actual_name != expected_name:
                    continue
            records.append(node)
        return records

    async def get_campaign(self, record_id: UUID) -> CRMRecord[CampaignData]:
        """Read and validate one managed Campaign by its remote UUID."""
        return await self._get_typed("malgCampaigns", record_id, CampaignData, "campaign")

    async def get_icp(self, record_id: UUID) -> CRMRecord[ICPData]:
        """Read and validate one managed ICP and its actual Campaign parent."""
        return await self._get_typed("malgIcps", record_id, ICPData, "icp", parent="campaign")

    async def get_account(self, record_id: UUID) -> CRMRecord[AccountData]:
        """Read and validate one native Company by its remote UUID."""
        return await self._get_typed("companies", record_id, AccountData, "account")

    async def get_person(self, record_id: UUID) -> CRMRecord[PersonData]:
        """Read and validate one native Person and its actual Company parent."""
        return await self._get_typed("people", record_id, PersonData, "person", parent="company")

    async def _get_typed(
        self,
        connection: str,
        record_id: UUID,
        model: type[BaseModel],
        kind: str,
        *,
        parent: str | None = None,
    ) -> CRMRecord[Any]:
        """Fetch a complete allowlisted projection and convert it to a typed envelope."""

        filter_type = _CONNECTION_FILTER_TYPES[connection]
        projection = _projection(kind, parent)
        query = f"""
        query Record($filter: {filter_type}!) {{
          {connection}(first: 2, filter: $filter) {{
            edges {{ node {{ id updatedAt deletedAt {projection} }} }}
          }}
        }}
        """
        data = await self.graphql(
            query,
            {"filter": {"id": {"eq": str(record_id)}}},
        )
        connection_data = data.get(connection)
        if not isinstance(connection_data, Mapping):
            raise TwentySchemaIncompatible("Twenty returned an invalid record connection.")
        edges = connection_data.get("edges")
        if not isinstance(edges, list) or len(edges) > 1:
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        if not edges:
            raise TwentyRecordMissing("crm_record_missing")
        node = edges[0].get("node") if isinstance(edges[0], Mapping) else None
        if not isinstance(node, Mapping) or str(node.get("id")) != str(record_id):
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        return _parse_typed_node(kind, node, model, parent)

    async def find_companies(
        self, *, domain_url: str | None, linkedin_url: str | None
    ) -> list[dict[str, Any]]:
        """Find at most 50 Companies by observed domain or LinkedIn identity links."""
        filters = []
        normalized_domain = normalize_domain(domain_url) if domain_url else None
        normalized_linkedin = (
            normalize_linkedin(linkedin_url, person=False) if linkedin_url else None
        )
        if normalized_domain:
            filters.extend(
                [
                    {"domainName": {"primaryLinkUrl": {"ilike": f"%{normalized_domain}%"}}},
                    _nonempty_native_array("domainName", "secondaryLinks"),
                ]
            )
        if normalized_linkedin:
            filters.extend(
                [
                    {
                        "linkedinLink": {
                            "primaryLinkUrl": {
                                "ilike": f"%{normalized_linkedin.rstrip('/').rsplit('/', 1)[-1]}%"
                            }
                        }
                    },
                    _nonempty_native_array("linkedinLink", "secondaryLinks"),
                ]
            )
        if not filters:
            return []
        data = await self.graphql(_COMPANY_IDENTITY_QUERY, {"filter": {"or": filters}})
        connection = data.get("companies")
        if not isinstance(connection, Mapping):
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        page_info = connection.get("pageInfo")
        if not isinstance(page_info, Mapping) or not isinstance(page_info.get("hasNextPage"), bool):
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        if page_info["hasNextPage"]:
            raise TwentyConflict("crm_identity_conflict")
        edges = connection.get("edges")
        if not isinstance(edges, list):
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        records = []
        for edge in edges:
            node = edge.get("node") if isinstance(edge, Mapping) else None
            if not isinstance(node, Mapping) or not isinstance(node.get("id"), str):
                raise TwentySchemaIncompatible("crm_schema_incompatible")
            try:
                UUID(node["id"])
                _parse_datetime(node.get("updatedAt"))
                _observed_composites(node)
            except (ValueError, TypeError, AttributeError) as error:
                raise TwentySchemaIncompatible("crm_schema_incompatible") from error
            if not (
                (
                    normalized_domain
                    and _has_link_identity(node.get("domainName"), normalized_domain, domain=True)
                )
                or (
                    normalized_linkedin
                    and _has_link_identity(
                        node.get("linkedinLink"), normalized_linkedin, person=False
                    )
                )
            ):
                continue
            records.append(dict(node))
        return records

    async def list_records(
        self,
        kind: str,
        *,
        first: int = 50,
        after: str | None = None,
        record_filter: Mapping[str, Any] | None = None,
    ) -> tuple[list[CRMRecord[Any]], str | None]:
        """List one supported object using the API router's generic contract."""
        specs: dict[str, tuple[str, type[BaseModel], str | None]] = {
            "campaign": ("malgCampaigns", CampaignData, None),
            "icp": ("malgIcps", ICPData, "campaign"),
            "account": ("companies", AccountData, None),
            "person": ("people", PersonData, "company"),
        }
        spec = specs.get(kind)
        if spec is None:
            raise ValueError(f"Unsupported Twenty record kind {kind!r}.")
        page = await self._list_typed(
            spec[0], kind, spec[1], cursor=after, limit=first, filter=record_filter, parent=spec[2]
        )
        return page.items, page.next_cursor

    async def fill_missing(
        self,
        object_name: str,
        record_id: str,
        observed_updated_at: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Atomically fill one empty scalar or composite at an observed version."""
        if len(fields) != 1:
            raise ValueError("fill_missing accepts exactly one independent field.")
        mutation_name, input_name, filter_name, supported_fields = _missing_field_operation(
            object_name
        )
        field_name = next(iter(fields))
        if field_name not in supported_fields:
            raise ValueError(f"Unsupported guarded Twenty field {field_name!r}.")
        mutation = f"""
        mutation Fill($data: {input_name}!, $filter: {filter_name}!) {{
          {mutation_name}(data: $data, filter: $filter) {{ id updatedAt }}
        }}
        """

        def blank(component: str | None = None, *, array: bool = False) -> dict[str, Any]:
            predicates = [{"is": "NULL"}, {"like": "[]"} if array else {"eq": ""}]
            return {
                "or": [
                    {field_name: {component: predicate} if component else predicate}
                    for predicate in predicates
                ]
            }

        if field_name in {"jobTitle", "malgSector"}:
            empty_predicate = blank()
        elif field_name in {"domainName", "linkedinLink"}:
            empty_predicate = {
                "and": [
                    blank("primaryLinkUrl"),
                    blank("primaryLinkLabel"),
                    blank("secondaryLinks", array=True),
                ]
            }
        elif field_name == "emails":
            empty_predicate = {
                "and": [blank("primaryEmail"), blank("additionalEmails", array=True)]
            }
        elif field_name == "annualRevenue":
            empty_predicate = {
                "and": [{field_name: {"amountMicros": {"is": "NULL"}}}, blank("currencyCode")]
            }
        elif field_name == "name":
            if not isinstance(fields[field_name], Mapping) or set(fields[field_name]) != {
                "lastName"
            }:
                raise ValueError("FullName hydration only permits a missing lastName component.")
            empty_predicate = blank("lastName")
        else:
            empty_predicate = {field_name: {"is": "NULL"}}
        data = await self.graphql(
            mutation,
            {
                "data": dict(fields),
                "filter": {
                    "and": [
                        {"id": {"eq": record_id}},
                        {"updatedAt": {"eq": observed_updated_at}},
                        empty_predicate,
                    ]
                },
            },
        )
        updated = data.get(mutation_name)
        if not isinstance(updated, list) or len(updated) > 1:
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        if not updated:
            raise TwentyConflict("crm_input_changed")
        if not isinstance(updated[0], dict) or updated[0].get("id") != record_id:
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        return updated[0]

    async def list_campaigns(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> CRMPage[CampaignData]:
        """Page managed Campaigns in stable UUID order with bounded continuation."""
        return await self._list_typed(
            "malgCampaigns", "campaign", CampaignData, cursor=cursor, limit=limit
        )

    async def list_icps(
        self, *, campaign_id: UUID | None = None, cursor: str | None = None, limit: int = 50
    ) -> CRMPage[ICPData]:
        """Page typed ICPs, optionally restricted to an actual Campaign UUID."""
        filt = {"campaignId": {"eq": str(campaign_id)}} if campaign_id else None
        return await self._list_typed(
            "malgIcps", "icp", ICPData, cursor=cursor, limit=limit, filter=filt, parent="campaign"
        )

    async def list_accounts(
        self, *, query: str | None = None, cursor: str | None = None, limit: int = 50
    ) -> CRMPage[AccountData]:
        """Search Company names case-insensitively; reject malformed page records."""
        filt = {"name": {"ilike": f"%{query}%"}} if query else None
        return await self._list_typed(
            "companies", "account", AccountData, cursor=cursor, limit=limit, filter=filt
        )

    async def list_people(
        self, *, company_id: UUID | None = None, cursor: str | None = None, limit: int = 50
    ) -> CRMPage[PersonData]:
        """Page native People, optionally restricted to their current Company UUID."""
        filt = {"companyId": {"eq": str(company_id)}} if company_id else None
        return await self._list_typed(
            "people",
            "person",
            PersonData,
            cursor=cursor,
            limit=limit,
            filter=filt,
            parent="company",
        )

    async def _list_typed(
        self,
        connection: str,
        kind: str,
        model: type[BaseModel],
        *,
        cursor: str | None,
        limit: int,
        filter: Mapping[str, Any] | None = None,
        parent: str | None = None,
    ) -> CRMPage[Any]:
        if not 1 <= limit <= 50:
            raise ValueError("Twenty list limit must be between 1 and 50.")
        query = f"""query Records($first: Int!, $after: String, $filter: {_CONNECTION_FILTER_TYPES[connection]}) {{
          {connection}(first: $first, after: $after, filter: $filter, orderBy: [{{ id: AscNullsFirst }}]) {{
            edges {{ node {{ id updatedAt {_projection(kind, parent)} }} }}
            pageInfo {{ hasNextPage endCursor }}
          }}
        }}"""
        data = await self.graphql(query, {"first": limit, "after": cursor, "filter": filter})
        conn = data.get(connection)
        page = conn.get("pageInfo") if isinstance(conn, Mapping) else None
        edges = conn.get("edges") if isinstance(conn, Mapping) else None
        if (
            not isinstance(edges, list)
            or not isinstance(page, Mapping)
            or not isinstance(page.get("hasNextPage"), bool)
        ):
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        next_cursor = page.get("endCursor")
        if page["hasNextPage"] and not isinstance(next_cursor, str):
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        items = []
        for edge in edges:
            node = edge.get("node") if isinstance(edge, Mapping) else None
            if not isinstance(node, Mapping):
                raise TwentySchemaIncompatible("crm_schema_incompatible")
            items.append(_parse_typed_node(kind, node, model, parent))
        return CRMPage(items=items, next_cursor=next_cursor if page["hasNextPage"] else None)

    async def has_membership(self, company_id: UUID, icp_id: UUID) -> bool:
        """Check the explicit Company-to-ICP membership relation."""
        data = await self.graphql(
            """
            query Membership($filter: MalgMembershipFilterInput!) {
              malgMemberships(first: 1, filter: $filter) { edges { node { id } } }
            }
            """,
            {
                "filter": {
                    "and": [
                        {"companyId": {"eq": str(company_id)}},
                        {"icpId": {"eq": str(icp_id)}},
                    ]
                }
            },
        )
        connection = data.get("malgMemberships")
        edges = connection.get("edges") if isinstance(connection, Mapping) else None
        if not isinstance(edges, list) or len(edges) > 1:
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        if edges:
            node = edges[0].get("node") if isinstance(edges[0], Mapping) else None
            try:
                if not isinstance(node, Mapping):
                    raise ValueError("missing membership")
                UUID(node["id"])
            except (KeyError, ValueError, TypeError, AttributeError) as error:
                raise TwentySchemaIncompatible("crm_schema_incompatible") from error
        return bool(edges)


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing updatedAt")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("updatedAt must include its timezone")
    return parsed


def _projection(kind: str, parent: str | None = None) -> str:
    parent_projection = f"{parent} {{ id }}" if parent else ""
    if kind == "campaign":
        return f"name objective {parent_projection}"
    if kind == "icp":
        return f"name sector geography employeesMin employeesMax buyerRole workflow {parent_projection}"
    if kind == "account":
        return "name malgSector malgEmployees annualRevenue { amountMicros currencyCode } domainName { primaryLinkLabel primaryLinkUrl secondaryLinks { label url } } linkedinLink { primaryLinkLabel primaryLinkUrl secondaryLinks { label url } }"
    return f"name {{ firstName lastName }} jobTitle emails {{ primaryEmail additionalEmails }} linkedinLink {{ primaryLinkLabel primaryLinkUrl secondaryLinks {{ label url }} }} {parent_projection}"


def _parse_typed_node(
    kind: str, node: Mapping[str, Any], model: type[BaseModel], parent: str | None
) -> CRMRecord[Any]:
    record_id = node.get("id")
    if not isinstance(record_id, str):
        raise TwentySchemaIncompatible("crm_schema_incompatible")
    if node.get("deletedAt") is not None:
        raise TwentyRecordMissing("crm_record_missing")
    try:
        values = _map_projection(kind, node)
        parent_id = _parent_id(node, parent)
        domain = _primary_link(node.get("domainName")) if kind == "account" else None
        return CRMRecord(
            id=UUID(record_id),
            data=model.model_validate(values),
            updated_at=_parse_datetime(node.get("updatedAt")),
            campaign_id=parent_id if parent == "campaign" else None,
            company_id=parent_id if parent == "company" else None,
            observed_composites=_observed_composites(node),
            official_domain=normalize_domain(domain) if domain else None,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise TwentySchemaIncompatible("crm_schema_incompatible") from error


def _observed_composites(node: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Preserve complete native components per field for safe hydration predicates."""
    observed: dict[str, dict[str, Any]] = {}
    for key in ("domainName", "linkedinLink", "emails", "name", "annualRevenue"):
        value = node.get(key)
        if value is None or (key == "name" and isinstance(value, str)):
            continue
        if not isinstance(value, Mapping):
            raise ValueError("invalid composite observation")
        if key in {"domainName", "linkedinLink"}:
            for scalar in ("primaryLinkLabel", "primaryLinkUrl"):
                if value.get(scalar) is not None and not isinstance(value.get(scalar), str):
                    raise ValueError("invalid link component")
            links = value.get("secondaryLinks")
            if links is not None and (
                not isinstance(links, list)
                or any(
                    not isinstance(link, Mapping)
                    or (link.get("label") is not None and not isinstance(link.get("label"), str))
                    or (link.get("url") is not None and not isinstance(link.get("url"), str))
                    for link in links
                )
            ):
                raise ValueError("invalid secondary links")
        if key == "emails":
            if value.get("primaryEmail") is not None and not isinstance(
                value.get("primaryEmail"), str
            ):
                raise ValueError("invalid primary email")
            emails = value.get("additionalEmails")
            if emails is not None and (
                not isinstance(emails, list) or any(not isinstance(email, str) for email in emails)
            ):
                raise ValueError("invalid additional emails")
        if key == "name":
            if value.get("firstName") is not None and not isinstance(value.get("firstName"), str):
                raise ValueError("invalid first name")
            if value.get("lastName") is not None and not isinstance(value.get("lastName"), str):
                raise ValueError("invalid last name")
        observed[key] = dict(value)
    return observed


def _map_projection(kind: str, node: Mapping[str, Any]) -> dict[str, Any]:
    if kind == "campaign":
        return {"name": node["name"], "objective": node["objective"]}
    if kind == "icp":
        return {
            "name": node["name"],
            "sector": node["sector"],
            "geography": node["geography"],
            "employees_min": node.get("employeesMin"),
            "employees_max": node.get("employeesMax"),
            "buyer_role": node["buyerRole"],
            "workflow": node["workflow"],
        }
    if kind == "account":
        annual = node.get("annualRevenue")
        annual_value = None
        if annual is not None:
            if not isinstance(annual, Mapping):
                raise ValueError("invalid currency composite")
            micros = annual.get("amountMicros")
            currency = annual.get("currencyCode")
            if micros is not None or currency not in (None, ""):
                if isinstance(micros, bool) or not isinstance(micros, (int, str)):
                    raise ValueError("invalid currency micros")
                text = str(micros)
                if not re.fullmatch(r"0|[1-9][0-9]*", text) or int(text) > _MAX_CURRENCY_MICROS:
                    raise ValueError("invalid currency micros")
                if not isinstance(currency, str):
                    raise ValueError("missing currency")
                annual_value = Money(
                    amount=Decimal(text) / Decimal(1_000_000), currency_code=currency
                )
        domain = _primary_link(node.get("domainName"))
        return {
            "name": node["name"],
            "sector": node.get("malgSector") or None,
            "employees": node.get("malgEmployees"),
            "annual_revenue": annual_value,
            "website": f"https://{normalize_domain(domain)}" if domain else None,
            "linkedin_url": _primary_link(node.get("linkedinLink")),
        }
    name = node.get("name")
    if not isinstance(name, Mapping):
        raise ValueError("invalid full name composite")
    emails = node.get("emails")
    if not isinstance(emails, Mapping):
        raise ValueError("invalid emails composite")
    return {
        "first_name": name.get("firstName") or "",
        "last_name": name.get("lastName") or None,
        "job_title": node.get("jobTitle") or None,
        "email": emails.get("primaryEmail") or None,
        "linkedin_url": _primary_link(node.get("linkedinLink")),
    }


def _parent_id(node: Mapping[str, Any], parent: str | None) -> UUID | None:
    if parent is None:
        return None
    value = node.get(parent)
    if value is None and parent == "company":
        return None
    if not isinstance(value, Mapping) or not isinstance(value.get("id"), str):
        raise ValueError("missing parent relation")
    return UUID(value["id"])


def _primary_link(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("invalid links composite")
    primary = value.get("primaryLinkUrl")
    if primary is not None and not isinstance(primary, str):
        raise ValueError("invalid primary link")
    return primary or None


def _nonempty_native_array(field: str, component: str) -> dict[str, Any]:
    """Include all secondary identities because native RawJson has no case-insensitive filter."""
    return {
        "not": {
            "or": [
                {field: {component: {"is": "NULL"}}},
                {field: {component: {"like": "[]"}}},
            ]
        }
    }


def _has_link_identity(
    value: object, expected: str, *, domain: bool = False, person: bool = False
) -> bool:
    """Match primary or secondary native links after conservative normalization."""
    if value is None:
        return False
    if not isinstance(value, Mapping):
        raise TwentySchemaIncompatible("crm_schema_incompatible")
    candidates: list[object] = [value.get("primaryLinkUrl")]
    secondary = value.get("secondaryLinks", [])
    if not isinstance(secondary, list):
        raise TwentySchemaIncompatible("crm_schema_incompatible")
    for link in secondary:
        if not isinstance(link, Mapping):
            raise TwentySchemaIncompatible("crm_schema_incompatible")
        candidates.append(link.get("url"))
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate:
            continue
        try:
            normalized = (
                normalize_domain(candidate)
                if domain
                else normalize_linkedin(candidate, person=person)
            )
        except ValueError as error:
            raise TwentySchemaIncompatible("crm_schema_incompatible") from error
        if normalized == expected:
            return True
    return False


class TwentyRecordMissing(TwentyError):
    """The requested record does not exist or is soft-deleted."""


class TwentySchemaIncompatible(TwentyError):
    """Twenty returned a projection that cannot satisfy the typed contract."""


class TwentyConflict(TwentyError):
    """A guarded remote write did not match its expected version or empty field."""


def _record_operation(object_name: str) -> tuple[str, str]:
    """Return a trusted schema-derived mutation identifier for one supported object."""
    try:
        return _RECORD_OPERATIONS[object_name]
    except KeyError as error:
        raise ValueError(f"Unsupported Twenty object {object_name!r}.") from error


def _missing_field_operation(
    object_name: str,
) -> tuple[str, str, str, frozenset[str]]:
    """Return a trusted guarded-update mutation for one supported object."""
    try:
        return _MISSING_FIELD_OPERATIONS[object_name]
    except KeyError as error:
        raise ValueError(f"Unsupported guarded Twenty object {object_name!r}.") from error
