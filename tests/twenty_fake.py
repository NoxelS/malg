"""Stateful offline Twenty HTTP boundary for publication and recovery regressions."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from malg.config import TwentyConfig
from malg.core.models.jobs import CampaignResearchJobRequest, ResearchJobRequest
from malg.crm.client import TwentyClient
from malg.crm.publisher import CrmPublisher
from malg.crm.schema import contract_hash, load_manifest
from malg.database.jobs import claim_next_job, enqueue_job
from malg.database.models import Base

_CONNECTIONS = {
    "malgCampaign": "malgCampaigns",
    "malgIcp": "malgIcps",
    "company": "companies",
    "person": "people",
    "malgMembership": "malgMemberships",
}
_NAMES = {
    "Company": "company",
    "Person": "person",
    "MalgCampaign": "malgCampaign",
    "MalgIcp": "malgIcp",
    "MalgMembership": "malgMembership",
}


def matches(value: Any, predicate: Mapping[str, Any]) -> bool:
    """Evaluate native filter semantics against state, not an echoed request body."""
    for key, expected in predicate.items():
        if key == "and":
            if not all(matches(value, item) for item in expected):
                return False
        elif key == "or":
            if not any(matches(value, item) for item in expected):
                return False
        elif key == "not":
            if matches(value, expected):
                return False
        elif key == "is":
            if expected != "NULL" or value is not None:
                return False
        elif key == "eq":
            if value != expected:
                return False
        elif key in {"like", "ilike"}:
            actual = value if isinstance(value, str) else json.dumps(value)
            pattern = re.escape(expected).replace("%", ".*").replace("_", ".")
            if re.fullmatch(pattern, actual, re.IGNORECASE if key == "ilike" else 0) is None:
                return False
        elif not matches(value.get(key) if isinstance(value, Mapping) else None, expected):
            return False
    return True


class RemoteTwenty:
    """Keep real record state and inject faults only at the external HTTP boundary."""

    def __init__(self, metadata: dict[str, Any]) -> None:
        self.metadata = metadata
        self.records: dict[tuple[str, str], dict[str, Any]] = {}
        self.creates: list[tuple[str, str]] = []
        self.updates = 0
        self.reject_once: set[str] = set()
        self.lose_response: set[str] = set()
        self.after_create: Callable[[], None] | None = None
        self.before_fill: Callable[[], Awaitable[None]] | None = None
        self.before_read: Callable[[str], None] | None = None
        self.unavailable = False
        self._clock = datetime(2026, 1, 1, tzinfo=UTC)

    def add(self, object_name: str, fields: dict[str, Any], record_id: str | None = None) -> str:
        """Seed or create an observed native record with complete empty composites."""
        identifier = record_id or str(uuid4())
        record: dict[str, Any] = {"id": identifier, "deletedAt": None}
        if object_name == "company":
            record.update(
                name="Company",
                malgSector=None,
                malgEmployees=None,
                annualRevenue={"amountMicros": None, "currencyCode": None},
                domainName={"primaryLinkLabel": "", "primaryLinkUrl": "", "secondaryLinks": []},
                linkedinLink={"primaryLinkLabel": "", "primaryLinkUrl": "", "secondaryLinks": []},
            )
        elif object_name == "person":
            record.update(
                name={"firstName": "", "lastName": ""},
                jobTitle=None,
                companyId=None,
                emails={"primaryEmail": "", "additionalEmails": []},
                linkedinLink={"primaryLinkLabel": "", "primaryLinkUrl": "", "secondaryLinks": []},
            )
        self.records[object_name, identifier] = record
        self.edit(object_name, identifier, fields)
        return identifier

    def edit(self, object_name: str, record_id: str, fields: Mapping[str, Any]) -> None:
        """Apply a native component update, preserving unspecified sibling components."""
        record = self.records[object_name, record_id]
        for field, value in fields.items():
            if isinstance(value, Mapping) and isinstance(record.get(field), Mapping):
                record[field] = {**record[field], **copy.deepcopy(value)}
            else:
                record[field] = copy.deepcopy(value)
        self._clock += timedelta(microseconds=1)
        record["updatedAt"] = self._clock.isoformat()

    async def handle(self, request: httpx.Request) -> httpx.Response:
        """Execute bounded metadata/read/create/atomic-update GraphQL requests."""
        if self.unavailable:
            return httpx.Response(503)
        if request.url.path == "/metadata":
            return httpx.Response(200, json=self.metadata)
        body = json.loads(request.content)
        query, variables = body["query"], body["variables"]
        create = re.search(r"\bcreate(Company|Person|MalgCampaign|MalgIcp|MalgMembership)\(", query)
        if create:
            operation = create.group(1)
            object_name = _NAMES[operation]
            fields = dict(variables["data"])
            identifier = fields.pop("id")
            if object_name in self.reject_once:
                self.reject_once.remove(object_name)
                return httpx.Response(200, json={"errors": [{"message": "rejected"}]})
            if (object_name, identifier) in self.records:
                return httpx.Response(200, json={"errors": [{"message": "duplicate ID"}]})
            self.add(object_name, fields, identifier)
            self.creates.append((object_name, identifier))
            if self.after_create:
                self.after_create()
            if object_name in self.lose_response:
                self.lose_response.remove(object_name)
                raise httpx.ReadTimeout("response lost", request=request)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "create" + operation: copy.deepcopy(self.records[object_name, identifier])
                    }
                },
            )
        update = re.search(r"\b(updateCompanies|updatePeople)\(", query)
        if update:
            if self.before_fill:
                await self.before_fill()
            object_name = "company" if update.group(1) == "updateCompanies" else "person"
            updated = []
            for (kind, identifier), record in self.records.items():
                if kind == object_name and matches(record, variables["filter"]):
                    self.edit(kind, identifier, variables["data"])
                    self.updates += 1
                    updated.append(copy.deepcopy(record))
            return httpx.Response(200, json={"data": {update.group(1): updated}})
        for object_name, connection in _CONNECTIONS.items():
            if re.search(r"\b" + connection + r"\(", query):
                if self.before_read:
                    self.before_read(object_name)
                records = []
                for (kind, _), stored in self.records.items():
                    if kind != object_name or not matches(stored, variables.get("filter") or {}):
                        continue
                    record = copy.deepcopy(stored)
                    for relation in ("campaign", "company", "icp"):
                        if relation + "Id" in record:
                            record[relation] = (
                                {"id": record[relation + "Id"]} if record[relation + "Id"] else None
                            )
                    records.append(record)
                records.sort(key=lambda row: row["id"])
                offset = int(variables.get("after") or 0)
                limit = variables.get("first", 50)
                page = records[offset : offset + limit]
                more = offset + limit < len(records)
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            connection: {
                                "edges": [{"node": row} for row in page],
                                "pageInfo": {
                                    "hasNextPage": more,
                                    "endCursor": str(offset + limit) if more else None,
                                },
                            }
                        }
                    },
                )
        raise AssertionError("Unexpected remote operation")


def publication_fixture(metadata: dict[str, Any], request: ResearchJobRequest | None = None):
    """Construct real persistence, a live claim, adapter and publisher around fake HTTP."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    remote = RemoteTwenty(metadata)
    http = httpx.AsyncClient(
        base_url="https://twenty.test", transport=httpx.MockTransport(remote.handle)
    )
    config = TwentyConfig(
        "https://twenty.test", "https://twenty.test", "offline-key", "workspace", max_retries=0
    )
    client = TwentyClient(config, http)
    publisher = CrmPublisher(sessions, config, client)
    with sessions.begin() as session:
        job = enqueue_job(request or CampaignResearchJobRequest(), session)
        manifest = load_manifest()
        job.contract_version, job.contract_hash = (
            manifest["contract_version"],
            contract_hash(manifest),
        )
    with sessions.begin() as session:
        claimed = claim_next_job(session, "worker", datetime.now(UTC), 60, 3)
        assert claimed is not None
        assert claimed.job_id == job.job_id
    return engine, sessions, remote, http, client, publisher, claimed
