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
from nooa import Agent  # type: ignore[attr-defined]  # NOOA re-exports Agent dynamically.
from nooa.mcp.tool import MCPTool, MCPToolSpec, _make_dynamic_class

from malg.config import BrowserConfig, get_browser_config, get_search_config, load_settings
from malg.core.budget import reserve_external_attempt
from malg.core.retrieval import RetrievalService
from malg.core.web_search import SearxngSearchClient
from malg.utils.console_progress import ConsoleProgress


class LoggedMCPTool(MCPTool):
    """MCP tool base that emits safe progress messages around remote calls."""

    def __init__(
        self,
        client: Any,
        server_name: str,
        refresh_ctx: dict[str, Any] | None = None,
        *,
        progress: ConsoleProgress | None = None,
        recorder: Any = None,
    ) -> None:
        super().__init__(client, server_name, refresh_ctx)
        self._progress = progress or ConsoleProgress()
        self._recorder = recorder

    async def _call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call MCP while reporting safe progress and raw trace boundaries."""
        reserve_external_attempt("fetch")
        clean_arguments = arguments or {}
        if self._recorder:
            self._recorder(
                "mcp_call_started",
                {
                    "server_name": self._server_name,
                    "tool_name": tool_name,
                    "arguments": clean_arguments,
                },
            )
        call_id = self._progress.mcp_started(self._server_name, tool_name, clean_arguments)
        try:
            result = await super()._call_tool(tool_name, clean_arguments)
        except Exception as exc:
            self._progress.mcp_failed(call_id, self._server_name, tool_name, exc)
            if self._recorder:
                self._recorder(
                    "mcp_call_failed",
                    {
                        "server_name": self._server_name,
                        "tool_name": tool_name,
                        "arguments": clean_arguments,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "traceback": __import__("traceback").format_exc(),
                    },
                )
            raise
        self._progress.mcp_finished(call_id, self._server_name, tool_name, result)
        if self._recorder:
            self._recorder(
                "mcp_call_succeeded",
                {
                    "server_name": self._server_name,
                    "tool_name": tool_name,
                    "arguments": clean_arguments,
                    "result": result,
                },
            )
        return result


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
        async with (
            httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(self._timeout_seconds, connect=5.0),
            ) as http_client,
            streamable_http_client(
                url=self._url,
                http_client=http_client,
                terminate_on_close=False,
            ) as (read, write, get_session_id),
            ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=self._timeout_seconds)
            ) as session,
        ):
            await session.initialize()
            session_id = get_session_id()
            if session_id:
                self._session_id = session_id
            yield session

    async def aclose(self) -> None:
        """Close the remote Lightpanda session, if one was established."""
        if self._session_id is None:
            return
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_seconds, connect=5.0)
        ) as client:
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


def create_browser_tool(config: BrowserConfig, recorder: Any = None) -> MCPTool:
    """Discover Lightpanda tools and return a per-agent, stateful tool object."""
    client = PersistentMCPStreamableHTTPClient(config)

    async def list_tools() -> Any:
        async with client.connect_to_server() as session:
            return await session.list_tools()

    try:
        tools_result = _run_sync(list_tools())
    except Exception as exc:
        raise RuntimeError(
            f"Unable to connect to the Lightpanda MCP endpoint at {config.url}."
        ) from exc

    tool_specs = [
        MCPToolSpec(
            name=tool.name,
            description=tool.description or "",
            input_schema=tool.inputSchema if isinstance(tool.inputSchema, dict) else {},
            required=set((tool.inputSchema or {}).get("required", []))
            if isinstance(tool.inputSchema, dict)
            else set(),
        )
        for tool in tools_result.tools
    ]
    tool_class = _make_dynamic_class("lightpanda", tool_specs, LoggedMCPTool)
    tool = object.__new__(tool_class)
    tool.__init__(client, "lightpanda", recorder=recorder)
    return tool


async def aclose_browser(browser: MCPTool) -> None:
    """Release a browser session outside an agent's callable interface."""
    client = getattr(browser, "_client", None)
    if isinstance(client, PersistentMCPStreamableHTTPClient):
        await client.aclose()


class BrowserSupport(Agent):
    """Base agent that supplies private discovery and an isolated Lightpanda browser.

    ``self.web_search`` discovers candidate sources through private SearXNG, while
    ``self.browser`` visits only selected URLs in an isolated Lightpanda session.
    """

    browser: MCPTool
    web_search: SearxngSearchClient
    retrieval: RetrievalService

    def __init__(self, *args: Any, recorder: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        config = get_browser_config(load_settings())
        if not config.enabled:
            raise RuntimeError("Browser support is disabled by configuration.")
        if recorder is None:
            self.browser = create_browser_tool(config)
        else:
            self.browser = create_browser_tool(config, recorder=recorder)
        self.web_search = SearxngSearchClient(get_search_config(load_settings()))
        self.retrieval = RetrievalService(self.web_search)
