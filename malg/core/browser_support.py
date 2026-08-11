"""Session-aware Lightpanda browser support for NOOA agents."""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from nooa import Agent
from nooa.mcp.tool import MCPTool, MCPToolSpec, _make_dynamic_class

from malg.config import BrowserConfig, get_browser_config, load_settings


class PersistentMCPStreamableHTTPClient:
    """Streamable HTTP client that retains Lightpanda's session between tool calls."""

    def __init__(self, config: BrowserConfig) -> None:
        self._url = config.url
        self._timeout_seconds = config.timeout_seconds
        self._session_id: str | None = None

    @property
    def session_id(self) -> str | None:
        """Return the Lightpanda session ID allocated to this agent."""
        return self._session_id

    @asynccontextmanager
    async def connect_to_server(self) -> AsyncGenerator[ClientSession, None]:
        """Open one MCP request channel and preserve its session identifier."""
        headers = {"Mcp-Session-Id": self._session_id} if self._session_id else None
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(self._timeout_seconds, connect=5.0),
        ) as http_client:
            async with streamable_http_client(
                url=self._url,
                http_client=http_client,
                terminate_on_close=False,
            ) as (read, write, get_session_id):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=self._timeout_seconds)) as session:
                    await session.initialize()
                    session_id = get_session_id()
                    if session_id:
                        self._session_id = session_id
                    yield session

    async def aclose(self) -> None:
        """Close the remote Lightpanda session, if one was established."""
        if self._session_id is None:
            return
        async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout_seconds, connect=5.0)) as client:
            response = await client.delete(self._url, headers={"Mcp-Session-Id": self._session_id})
            response.raise_for_status()
        self._session_id = None


def _run_sync(coro: Any) -> Any:
    """Run MCP discovery both inside and outside an existing event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def create_browser_tool(config: BrowserConfig) -> MCPTool:
    """Discover Lightpanda tools and return a per-agent, stateful tool object."""
    client = PersistentMCPStreamableHTTPClient(config)

    async def list_tools() -> Any:
        async with client.connect_to_server() as session:
            return await session.list_tools()

    try:
        tools_result = _run_sync(list_tools())
    except Exception as exc:
        raise RuntimeError(f"Unable to connect to the Lightpanda MCP endpoint at {config.url}.") from exc

    tool_specs = [
        MCPToolSpec(
            name=tool.name,
            description=tool.description or "",
            input_schema=tool.inputSchema if isinstance(tool.inputSchema, dict) else {},
            required=set((tool.inputSchema or {}).get("required", [])) if isinstance(tool.inputSchema, dict) else set(),
        )
        for tool in tools_result.tools
    ]
    tool_class = _make_dynamic_class("lightpanda", tool_specs, MCPTool)
    tool = object.__new__(tool_class)
    tool.__init__(client, "lightpanda")
    return tool


async def aclose_browser(browser: MCPTool) -> None:
    """Release a browser session outside an agent's callable interface."""
    client = browser._client
    if isinstance(client, PersistentMCPStreamableHTTPClient):
        await client.aclose()


class BrowserSupport(Agent):
    """Base agent that supplies an isolated Lightpanda browser as ``self.browser``."""

    browser: MCPTool

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        config = get_browser_config(load_settings())
        if not config.enabled:
            raise RuntimeError("Browser support is disabled by configuration.")
        self.browser = create_browser_tool(config)
