"""Behavioral checks for truthful Twenty metadata reconciliation."""

import asyncio
import copy
import json
from contextlib import redirect_stdout
from io import StringIO

import httpx
import pytest

from malg.config import TwentyConfig
from malg.crm.client import TwentyClient
from malg.crm.schema import SchemaConflict, inspect_remote, load_manifest, reconcile

MINIMAL = {
    "standard_objects": {},
    "custom_objects": {},
    "relations": [],
    "contract_version": 1,
}


def _client(
    metadata: dict, *, mutate: object | None = None
) -> tuple[TwentyClient, httpx.AsyncClient, list[int]]:
    mutations = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal metadata
        payload = json.loads(request.content)
        if "query Objects" in payload["query"]:
            return httpx.Response(200, json=metadata)
        mutations[0] += 1
        if callable(mutate):
            mutate(payload)
        return httpx.Response(200, json={"data": {"createOneObject": {"id": "created"}}})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://twenty.test")
    client = TwentyClient(
        TwentyConfig(
            base_url="https://twenty.test",
            public_url="https://twenty.test",
            api_key="schema-key",
            workspace_id="workspace",
        ),
        http,
    )
    return client, http, mutations


def _paged_client(pages: list[dict]) -> tuple[TwentyClient, httpx.AsyncClient]:
    remaining = iter(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": next(remaining)})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://twenty.test")
    return (
        TwentyClient(
            TwentyConfig(
                base_url="https://twenty.test",
                public_url="https://twenty.test",
                api_key="schema-key",
                workspace_id="workspace",
            ),
            http,
        ),
        http,
    )


def _relation_field(metadata: dict, relation: dict, object_key: str) -> dict:
    names = {
        relation["from"]: relation["field"],
        relation["to"]: relation["inverse"],
    }
    object_name = load_manifest()["custom_objects"].get(object_key, {}).get("name_singular")
    if object_name is None:
        object_name = load_manifest()["standard_objects"][object_key]["api_name"]
    for edge in metadata["data"]["objects"]["edges"]:
        node = edge["node"]
        if node["nameSingular"] == object_name:
            return next(field for field in node["fieldsList"] if field["name"] == names[object_key])
    raise AssertionError(f"missing fixture object {object_key}")


def _run_cli(
    monkeypatch: pytest.MonkeyPatch, metadata: dict, action: str
) -> tuple[int, str, list[int]]:
    import malg.crm.__main__ as cli

    _, http, mutations = _client(metadata)
    original = httpx.AsyncClient
    monkeypatch.setenv("MALG_TWENTY__BASE_URL", "https://twenty.test")
    monkeypatch.setenv("MALG_TWENTY__PUBLIC_URL", "https://twenty.test")
    monkeypatch.setenv("MALG_TWENTY__WORKSPACE_ID", "workspace")
    monkeypatch.setenv("MALG_TWENTY_SCHEMA__API_KEY", "test-schema")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: http)
    monkeypatch.setattr("sys.argv", ["malg-crm", "schema", action])
    captured = StringIO()
    try:
        with redirect_stdout(captured):
            code = cli.main()
        return code, captured.getvalue(), mutations
    finally:
        monkeypatch.setattr(httpx, "AsyncClient", original)


def test_metadata_without_page_info_fails_closed() -> None:
    client, http = _paged_client([{"objects": {"edges": []}}])
    try:
        with pytest.raises(SchemaConflict, match="pagination"):
            asyncio.run(inspect_remote(client))
    finally:
        asyncio.run(http.aclose())


def test_repeated_metadata_cursor_fails_closed() -> None:
    page = {"objects": {"edges": [], "pageInfo": {"hasNextPage": True, "endCursor": "same"}}}
    client, http = _paged_client([page, page])
    try:
        with pytest.raises(SchemaConflict, match="continuation cursor"):
            asyncio.run(inspect_remote(client))
    finally:
        asyncio.run(http.aclose())


def test_plan_is_pending_but_read_only() -> None:
    manifest = {
        **MINIMAL,
        "custom_objects": {
            "campaign": {
                "name_singular": "malgCampaign",
                "name_plural": "malgCampaigns",
                "fields": {"name": {"type": "TEXT", "nullable": False}},
            }
        },
    }
    client, http, mutations = _client(
        {"data": {"objects": {"edges": [], "pageInfo": {"hasNextPage": False}}}}
    )
    try:
        operations = asyncio.run(reconcile(client, manifest, apply=False))
        assert operations[0]["action"] == "create_object"
        assert mutations == [0]
    finally:
        asyncio.run(http.aclose())


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("foreign", "foreign"),
        ("target", "topology"),
        ("inverse", "wrong inverse"),
        ("join", "wrong join"),
    ],
)
def test_relation_conflicts_fail_closed_without_apply_mutations(
    twenty_metadata: dict, change: str, message: str
) -> None:
    metadata = copy.deepcopy(twenty_metadata)
    relation = load_manifest()["relations"][0]
    source = _relation_field(metadata, relation, relation["from"])
    inverse = _relation_field(metadata, relation, relation["to"])
    if change == "foreign":
        source["description"] = "Human-owned"
    elif change == "target":
        source["relation"]["targetObjectMetadata"]["id"] = "different-object"
    elif change == "inverse":
        source["relation"]["targetFieldMetadata"]["name"] = "other"
    else:
        inverse["settings"]["joinColumnName"] = "otherId"
    client, http, mutations = _client(metadata)
    try:
        with pytest.raises(SchemaConflict, match=message):
            asyncio.run(reconcile(client, load_manifest(), apply=True))
        assert mutations == [0]
    finally:
        asyncio.run(http.aclose())


def test_apply_requires_unconditional_compatible_readback(twenty_metadata: dict) -> None:
    metadata = copy.deepcopy(twenty_metadata)
    campaign = next(
        edge["node"]
        for edge in metadata["data"]["objects"]["edges"]
        if edge["node"]["nameSingular"] == "malgCampaign"
    )
    campaign["fieldsList"] = [
        field for field in campaign["fieldsList"] if field["name"] != "objective"
    ]
    client, http, mutations = _client(metadata)
    try:
        with pytest.raises(SchemaConflict, match="remained incomplete"):
            asyncio.run(reconcile(client, load_manifest(), apply=True))
        assert mutations == [1]
    finally:
        asyncio.run(http.aclose())


def test_cli_plan_reports_pending_but_exits_successfully(
    monkeypatch: pytest.MonkeyPatch, twenty_metadata: dict
) -> None:
    metadata = copy.deepcopy(twenty_metadata)
    campaign = next(
        edge["node"]
        for edge in metadata["data"]["objects"]["edges"]
        if edge["node"]["nameSingular"] == "malgCampaign"
    )
    campaign["fieldsList"] = [
        field for field in campaign["fieldsList"] if field["name"] != "objective"
    ]
    code, output, mutations = _run_cli(monkeypatch, metadata, "plan")
    payload = json.loads(output)
    assert code == 0
    assert payload["schema_compatible"] is False
    assert payload["operations"]
    assert mutations == [0]


def test_cli_check_pending_additions_exits_two(
    monkeypatch: pytest.MonkeyPatch, twenty_metadata: dict
) -> None:
    metadata = copy.deepcopy(twenty_metadata)
    campaign = next(
        edge["node"]
        for edge in metadata["data"]["objects"]["edges"]
        if edge["node"]["nameSingular"] == "malgCampaign"
    )
    campaign["fieldsList"] = [
        field for field in campaign["fieldsList"] if field["name"] != "objective"
    ]
    code, output, mutations = _run_cli(monkeypatch, metadata, "check")
    payload = json.loads(output)
    assert code == 2
    assert payload["schema_compatible"] is False
    assert payload["operations"]
    assert mutations == [0]


@pytest.mark.parametrize(
    "malformation", ["non_boolean_page", "malformed_edge", "duplicate_object", "duplicate_field"]
)
def test_incomplete_or_ambiguous_metadata_never_authorizes_apply(
    twenty_metadata, malformation
) -> None:
    metadata = copy.deepcopy(twenty_metadata)
    connection = metadata["data"]["objects"]
    if malformation == "non_boolean_page":
        connection["pageInfo"]["hasNextPage"] = "false"
    elif malformation == "malformed_edge":
        connection["edges"].append({"node": None})
    elif malformation == "duplicate_object":
        connection["edges"].append(copy.deepcopy(connection["edges"][0]))
    else:
        fields = connection["edges"][0]["node"]["fieldsList"]
        fields.append(copy.deepcopy(fields[0]))
    client, http, mutations = _client(metadata)
    try:
        with pytest.raises(SchemaConflict):
            asyncio.run(reconcile(client, load_manifest(), apply=True))
        assert mutations == [0]
    finally:
        asyncio.run(http.aclose())
