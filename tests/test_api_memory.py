"""Observable authenticated operational-memory API behavior."""

from __future__ import annotations

from nooa_memory.schema import Edge, EdgeType, Memory
from tests.fixtures import authenticated_client
from tests.test_memory_store import _engine

from malg.database.models import AgentMemory
from malg.database.session import make_session_factory


def test_memory_api_crud_owner_filter_pagination_archive_and_edges() -> None:
    engine = _engine()
    sessions = make_session_factory(engine)
    client = authenticated_client(engine)
    records = [
        Memory(id="one", content="first", owner="alice", edges=[]),
        Memory(id="two", content="second", owner="alice", edges=[]),
        Memory(id="other", content="unrelated", owner="bob", edges=[]),
    ]
    for record in records:
        response = client.post("/api/v1/memories", json=record.model_dump(mode="json"))
        assert response.status_code == 201
    duplicate = client.post("/api/v1/memories", json=records[0].model_dump(mode="json"))
    assert duplicate.status_code == 409

    listed = client.get("/api/v1/memories", params={"owner": "alice", "limit": 1})
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["owner"] == "alice"
    assert (
        client.get("/api/v1/memories", params={"owner": "alice", "offset": 1}).json()["items"][0][
            "id"
        ]
        == "one"
    )

    replacement = records[0].model_copy(
        update={
            "content": "updated",
            "edges": [Edge(target_id="two", type=EdgeType.RELATED)],
        }
    )
    updated = client.put("/api/v1/memories/one", json=replacement.model_dump(mode="json"))
    assert updated.status_code == 200
    assert updated.json()["content"] == "updated"
    assert updated.json()["edges"][0]["target_id"] == "two"
    assert (
        client.put(
            "/api/v1/memories/one",
            json=replacement.model_copy(update={"id": "other"}).model_dump(mode="json"),
        ).status_code
        == 422
    )

    with sessions() as session:
        row = session.get(AgentMemory, "two")
        assert row is not None
        row.archived = True
        session.commit()
    visible = client.get("/api/v1/memories").json()["items"]
    assert {item["id"] for item in visible} == {"one", "other"}
    archived = client.get("/api/v1/memories", params={"include_archived": True}).json()["items"]
    assert {item["id"] for item in archived} == {"other", "two", "one"}

    assert client.delete("/api/v1/memories/two").status_code == 204
    assert client.get("/api/v1/memories/two").status_code == 404
    assert client.delete("/api/v1/memories/missing").status_code == 404
    engine.dispose()
