"""Offline proofs for deterministic CRM adapter contracts."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from malg.config import TwentyConfig
from malg.crm.client import TwentyClient
from malg.crm.identity import normalize_domain, normalize_linkedin


def test_identity_normalization_is_conservative() -> None:
    """IDNA, www and tracking normalization preserve company boundaries."""
    assert normalize_domain("https://WWW.Bücher.example/path?q=1") == "xn--bcher-kva.example"
    assert normalize_domain("https://eu.example.com") != normalize_domain("https://example.com")
    assert normalize_linkedin("https://www.linkedin.com/company/acme/?trk=feed", person=False) == (
        "https://linkedin.com/company/acme/"
    )


def test_hydration_preserves_zero_and_composite_values() -> None:
    """Only genuinely empty fields are selected for remote updates."""
    from malg.crm.hydration import missing_fields

    assert missing_fields(
        {"employees": 0, "website": {"primaryLinkUrl": "", "secondaryLinks": []}},
        {"employees": 50, "website": {"primaryLinkUrl": "https://acme.test"}},
    ) == {"website": {"primaryLinkUrl": "https://acme.test"}}


def test_company_matching_rejects_ambiguous_identity() -> None:
    """Domain and LinkedIn evidence must identify the same unique company."""
    from malg.crm.matching import choose_company_match

    assert choose_company_match([{"id": "a"}], [{"id": "b"}]) == (None, "identity_sources_conflict")
    assert choose_company_match([{"id": "a"}, {"id": "b"}], []) == (
        None,
        "multiple_identity_matches",
    )
    assert choose_company_match([{"id": "a"}], [{"id": "a"}]) == ("a", None)


def test_lean_icp_requires_a_buyer_role_and_preserves_unknown_employee_bounds() -> None:
    """The managed ICP projection retains its buyer role without inventing a size."""
    from pydantic import ValidationError

    from malg.core.models.icp import ICPData

    icp = ICPData(
        name="Operations teams",
        sector="Logistics",
        geography="Germany",
        buyer_role="Operations director",
        workflow="Document intake",
    )
    assert icp.employees_min is None
    assert icp.employees_max is None

    with pytest.raises(ValidationError):
        ICPData(
            name="Operations teams",
            sector="Logistics",
            geography="Germany",
            workflow="Document intake",
        )


def test_research_observations_reject_fields_from_another_business_contract() -> None:
    """NOOA must receive a repairable validation error before returning an invalid ICP."""
    from pydantic import ValidationError

    from malg.core.models.icp import ICPData
    from malg.core.models.research import ResearchResult

    data = ICPData(
        name="Operations teams",
        sector="Logistics",
        geography="Germany",
        buyer_role="Operations director",
        workflow="Document intake",
    )
    with pytest.raises(ValidationError):
        ResearchResult[ICPData](
            outcome="partial",
            data=data,
            observations=[
                {
                    "field": "employees",
                    "text": "The page describes licensed users, not employee headcount.",
                    "excerpt_ids": ["source:0"],
                    "quote": "50-200 users",
                }
            ],
        )


def test_schema_reconciler_is_additive_and_idempotent() -> None:
    """Dry-run plans missing metadata; apply creates it with variable-bound mutations."""

    async def run() -> None:
        objects: list[dict[str, object]] = []
        mutations = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal mutations
            payload = json.loads(request.content)
            operation = payload["query"]
            if "query Objects" in operation:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "objects": {
                                "edges": [{"node": item} for item in objects],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    },
                )
            mutations += 1
            if "CreateObject" in operation:
                objects.append(
                    {
                        **payload["variables"]["input"]["object"],
                        "id": "object-id",
                        "fieldsList": [],
                    }
                )
                return httpx.Response(200, json={"data": {"createOneObject": {"id": "object-id"}}})
            field = payload["variables"]["input"]["field"]
            assert field["objectMetadataId"] == "object-id"
            objects[0]["fieldsList"].append({**field, "id": "field-id"})
            return httpx.Response(
                200, json={"data": {"createOneField": {"id": "field-id", "name": "name"}}}
            )

        from malg.crm.schema import reconcile

        manifest = {
            "standard_objects": {},
            "custom_objects": {
                "campaign": {
                    "name_singular": "malgCampaign",
                    "name_plural": "malgCampaigns",
                    "fields": {"name": {"type": "TEXT", "nullable": False}},
                }
            },
            "relations": [],
        }
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://twenty.test"
        )
        client = TwentyClient(
            TwentyConfig(
                base_url="https://twenty.test",
                public_url="https://twenty.test",
                api_key="schema-key",
                workspace_id="workspace",
            ),
            http,
        )

        dry_run = await reconcile(client, manifest, apply=False)
        assert [operation["action"] for operation in dry_run] == ["create_object", "create_field"]

        applied = await reconcile(client, manifest, apply=True)
        assert applied == dry_run
        assert mutations == 2
        assert await reconcile(client, manifest, apply=False) == []
        assert await reconcile(client, manifest, apply=True) == []
        assert mutations == 2
        await http.aclose()

    asyncio.run(run())
