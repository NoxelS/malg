"""Focused proofs for typed Twenty reads and transport safety."""

from __future__ import annotations

import asyncio
from uuid import UUID

import httpx
import pytest

from malg.config import TwentyConfig
from malg.crm.client import TwentyClient, TwentySchemaIncompatible, TwentyUnavailable

_ID = "11111111-1111-1111-1111-111111111111"


def _client(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://twenty.test")
    return TwentyClient(
        TwentyConfig("https://twenty.test", "https://twenty.test", "key", "ws"), http
    ), http


def test_typed_person_maps_parent_and_composites() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "people": {
                            "edges": [
                                {
                                    "node": {
                                        "id": _ID,
                                        "updatedAt": "2026-01-01T00:00:00Z",
                                        "name": {"firstName": "Ada", "lastName": "Lovelace"},
                                        "jobTitle": "CTO",
                                        "emails": {
                                            "primaryEmail": "ada@example.test",
                                            "additionalEmails": ["a@legacy.test"],
                                        },
                                        "linkedinLink": {
                                            "primaryLinkUrl": "https://linkedin.com/in/ada",
                                            "secondaryLinks": [
                                                {"label": "Bio", "url": "https://example.test/bio"}
                                            ],
                                        },
                                        "company": {"id": "22222222-2222-2222-2222-222222222222"},
                                    }
                                }
                            ]
                        }
                    }
                },
            )

        client, http = _client(handler)
        record = await client.get_person(UUID(_ID))
        assert record.company_id == UUID("22222222-2222-2222-2222-222222222222")
        assert record.data.first_name == "Ada"
        assert record.observed_composites["emails"]["additionalEmails"] == ["a@legacy.test"]
        await http.aclose()

    asyncio.run(run())


def test_mutation_transport_failure_is_not_retried() -> None:
    async def run() -> None:
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(503)

        client, http = _client(handler)
        with pytest.raises(TwentyUnavailable):
            await client.create_record("company", _ID, {"name": "Acme"})
        assert calls == 1
        await http.aclose()

    asyncio.run(run())


def test_fractional_or_oversized_currency_is_schema_incompatible() -> None:
    async def run() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "companies": {
                            "edges": [
                                {
                                    "node": {
                                        "id": _ID,
                                        "updatedAt": "2026-01-01T00:00:00Z",
                                        "name": "Acme",
                                        "annualRevenue": {
                                            "amountMicros": "1.5",
                                            "currencyCode": "USD",
                                        },
                                        "domainName": {
                                            "primaryLinkUrl": "https://acme.test",
                                            "secondaryLinks": [],
                                        },
                                        "linkedinLink": {
                                            "primaryLinkUrl": None,
                                            "secondaryLinks": [],
                                        },
                                    }
                                }
                            ]
                        }
                    }
                },
            )

        client, http = _client(handler)
        with pytest.raises(TwentySchemaIncompatible, match="crm_schema_incompatible"):
            await client.get_account(UUID(_ID))
        await http.aclose()

    asyncio.run(run())
