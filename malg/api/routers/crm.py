"""Authenticated operational read API for the external Twenty CRM."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from collections.abc import Mapping
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from malg.api.dependencies import CrmClientDependency
from malg.crm.client import (
    TwentyClient,
    TwentyError,
    TwentyRecordMissing,
    TwentySchemaIncompatible,
    TwentyUnavailable,
)
from malg.crm.contracts import CRMRecord
from malg.crm.schema import SchemaConflict, contract_hash, load_manifest

router = APIRouter(prefix="/api/v1/crm", tags=["crm"])


def _cursor_token(upstream: str, identity: object) -> str:
    """Bind a remote continuation to its connection and normalized filter."""
    raw = json.dumps({"cursor": upstream, "filter": identity}, sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str | None, identity: object) -> str | None:
    """Reject malformed and cross-query continuations before remote transport."""
    if value is None:
        return None
    try:
        if len(value) > 8192:
            raise ValueError
        decoded = json.loads(
            base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(decoded, Mapping):
            raise ValueError
        cursor = decoded.get("cursor")
        if decoded.get("filter") != identity or not isinstance(cursor, str) or not cursor:
            raise ValueError
        return cursor
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error) as error:
        raise HTTPException(status_code=422, detail="invalid_cursor") from error


def _summary(client: TwentyClient, record: CRMRecord[Any]) -> dict[str, object]:
    """Render a typed record without inventing an unverified Twenty UI route."""
    name = getattr(record.data, "name", None)
    if not isinstance(name, str) or not name.strip():
        name = " ".join(
            part.strip()
            for part in (
                getattr(record.data, "first_name", None),
                getattr(record.data, "last_name", None),
            )
            if isinstance(part, str) and part.strip()
        )
    return {
        "id": str(record.id),
        "display_name": name or str(record.id),
        "url": client.config.public_url,
    }


def _upstream_error(error: TwentyError) -> HTTPException:
    """Map sanitized adapter failures to the operational HTTP contract."""
    if isinstance(error, TwentyRecordMissing):
        return HTTPException(status_code=404, detail="crm_record_missing")
    if isinstance(error, TwentySchemaIncompatible):
        return HTTPException(status_code=502, detail="crm_schema_incompatible")
    return HTTPException(status_code=503, detail="crm_unavailable")


async def _list_records(
    client: TwentyClient,
    kind: str,
    cursor: str | None,
    record_filter: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Use the shared typed adapter with stable, filter-bound pagination."""
    identity = {"kind": kind, "filter": record_filter or {}}
    after = _decode_cursor(cursor, identity)
    try:
        records, next_cursor = await client.list_records(
            kind, first=50, after=after, record_filter=record_filter
        )
    except TwentyError as error:
        raise _upstream_error(error) from error
    if next_cursor is not None and (not next_cursor or next_cursor == after):
        raise HTTPException(status_code=502, detail="crm_schema_incompatible")
    return {
        "items": [_summary(client, record) for record in records],
        "next_cursor": _cursor_token(next_cursor, identity) if next_cursor else None,
    }


@router.get("/status")
async def crm_status(request: Request) -> dict[str, object]:
    """Bound an actual metadata check; local history remains independent."""
    manifest = load_manifest()
    client = getattr(request.app.state, "crm_client", None)
    base = {
        "available": False,
        "schema_compatible": False,
        "expected_contract_version": manifest["contract_version"],
        "expected_contract_hash": contract_hash(manifest),
        "public_url": client.config.public_url if isinstance(client, TwentyClient) else None,
    }
    if not isinstance(client, TwentyClient):
        return {**base, "reason": "crm_unavailable"}
    try:
        async with asyncio.timeout(client.config.timeout_seconds):
            observed = await client.check_contract()
    except (TimeoutError, TwentyUnavailable):
        return {**base, "reason": "crm_unavailable"}
    except (SchemaConflict, TwentySchemaIncompatible):
        return {**base, "available": True, "reason": "crm_schema_incompatible"}
    except TwentyError:
        return {**base, "reason": "crm_unavailable"}
    compatible = bool(observed["schema_compatible"])
    return {
        **base,
        "available": True,
        "schema_compatible": compatible,
        "reason": None if compatible else "crm_schema_incompatible",
    }


@router.get("/campaigns")
async def list_crm_campaigns(
    client: CrmClientDependency, cursor: Annotated[str | None, Query()] = None
) -> dict[str, object]:
    """List managed Campaigns using bounded remote pages."""
    return await _list_records(client, "campaign", cursor)


@router.get("/icps")
async def list_crm_icps(
    client: CrmClientDependency,
    campaign_id: Annotated[UUID | None, Query(alias="campaignId")] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> dict[str, object]:
    """List ICPs optionally scoped to their actual Campaign."""
    record_filter = {"campaignId": {"eq": str(campaign_id)}} if campaign_id else None
    return await _list_records(client, "icp", cursor, record_filter)


@router.get("/accounts")
async def list_crm_accounts(
    client: CrmClientDependency,
    query: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> dict[str, object]:
    """List Companies with an optional normalized case-insensitive name filter."""
    query = query.strip() if query else None
    record_filter = {"name": {"ilike": f"%{query}%"}} if query else None
    return await _list_records(client, "account", cursor, record_filter)


@router.get("/people")
async def list_crm_people(
    client: CrmClientDependency,
    account_id: Annotated[UUID | None, Query(alias="companyId")] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> dict[str, object]:
    """List native People scoped to their current Company."""
    record_filter = {"companyId": {"eq": str(account_id)}} if account_id else None
    return await _list_records(client, "person", cursor, record_filter)


async def _detail(client: TwentyClient, kind: str, record_id: UUID) -> dict[str, object]:
    """Expose typed business fields and host-owned parent references."""
    getter = {
        "campaign": client.get_campaign,
        "icp": client.get_icp,
        "account": client.get_account,
        "person": client.get_person,
    }[kind]
    try:
        record = cast(CRMRecord[Any], await getter(record_id))
    except TwentyError as error:
        raise _upstream_error(error) from error
    return {
        **_summary(client, record),
        "data": record.data.model_dump(mode="json"),
        "campaign_id": str(record.campaign_id) if record.campaign_id else None,
        "company_id": str(record.company_id) if record.company_id else None,
        "updated_at": record.updated_at.isoformat(),
    }


@router.get("/campaigns/{record_id}")
async def get_crm_campaign(record_id: UUID, client: CrmClientDependency) -> dict[str, object]:
    """Return one typed remote Campaign."""
    return await _detail(client, "campaign", record_id)


@router.get("/icps/{record_id}")
async def get_crm_icp(record_id: UUID, client: CrmClientDependency) -> dict[str, object]:
    """Return one typed remote ICP."""
    return await _detail(client, "icp", record_id)


@router.get("/accounts/{record_id}")
async def get_crm_account(record_id: UUID, client: CrmClientDependency) -> dict[str, object]:
    """Return one typed remote Company."""
    return await _detail(client, "account", record_id)


@router.get("/people/{record_id}")
async def get_crm_person(record_id: UUID, client: CrmClientDependency) -> dict[str, object]:
    """Return one typed native Person."""
    return await _detail(client, "person", record_id)
