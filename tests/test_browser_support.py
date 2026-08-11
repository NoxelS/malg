from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from malg.config import BrowserConfig
from malg.core import browser_support
from malg.core.browser_support import PersistentMCPStreamableHTTPClient


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def initialize(self) -> None:
        return None


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


class _FakeHTTPClient:
    calls: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> _FakeHTTPClient:
        self.calls.append(self.kwargs)
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def delete(self, url: str, *, headers: dict[str, str]) -> _FakeResponse:
        self.calls.append({"delete_url": url, "delete_headers": headers})
        return _FakeResponse()


def test_lightpanda_session_id_is_sent_on_later_calls(monkeypatch) -> None:
    _FakeHTTPClient.calls = []
    monkeypatch.setattr(browser_support.httpx, "AsyncClient", _FakeHTTPClient)
    monkeypatch.setattr(browser_support, "ClientSession", lambda *args, **kwargs: _FakeSession())

    @asynccontextmanager
    async def fake_transport(*, url: str, http_client: object, terminate_on_close: bool):
        assert terminate_on_close is False
        yield object(), object(), lambda: "agent-session"

    monkeypatch.setattr(browser_support, "streamable_http_client", fake_transport)
    client = PersistentMCPStreamableHTTPClient(BrowserConfig(enabled=True, url="http://lightpanda:9223/mcp", timeout_seconds=30))

    async def run() -> None:
        async with client.connect_to_server():
            pass
        async with client.connect_to_server():
            pass
        await client.aclose()

    asyncio.run(run())

    assert _FakeHTTPClient.calls[0]["headers"] is None
    assert _FakeHTTPClient.calls[1]["headers"] == {"Mcp-Session-Id": "agent-session"}
    assert _FakeHTTPClient.calls[-1]["delete_headers"] == {"Mcp-Session-Id": "agent-session"}
    assert client.session_id is None
