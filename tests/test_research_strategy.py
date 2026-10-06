"""Host retrieval tools preserve discoveries and never execute model-generated code."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from nooa.strategies.current_call import CurrentCall
from nooa.unifiedllm import LLMResponse, ToolCall
from test_retrieval import redirect_transport
from test_web_search import _config, _FakeHTTPClient

from malg.config import ResearchConfig
from malg.core.budget import BudgetExhausted
from malg.core.models.campaign import CampaignData
from malg.core.models.research import ResearchResult
from malg.core.research_strategy import RetrievalStrategy
from malg.core.retrieval import RetrievalService
from malg.core.web_search import SearchUnavailable, SearxngSearchClient


class _Events:
    def __init__(self):
        self.items = []

    def add(self, event, **kwargs):
        self.items.append(event)


class _Runtime:
    """Injected NOOA boundary; only explicit tool requests enter the strategy."""

    def __init__(self, actions):
        self.event_manager = _Events()
        self.actions = iter(actions)
        self.requests = []
        self.agent = SimpleNamespace()

    def get_generation_id(self):
        return "generation"

    def get_parent_generation_id(self):
        return None

    async def generate(self, **kwargs):
        self.requests.append(kwargs)
        action = next(self.actions)
        actions = action if isinstance(action, list) else [action]
        tool_calls = [
            ToolCall(
                id=f"{len(self.requests)}-{index}",
                name=name,
                arguments=args if isinstance(args, str) else json.dumps(args),
            )
            for index, (name, args) in enumerate(actions)
        ]
        return LLMResponse(
            content="",
            raw_response={},
            finish_reason="tool_calls",
            assistant_message={"role": "assistant", "content": ""},
            tool_calls=tool_calls,
        ), "response"

    async def execute_code(self, *args, **kwargs):
        raise AssertionError("Generated code must never execute")


def _call(**inputs):
    return CurrentCall(
        id="call",
        method_name="research",
        decorator="agent",
        kwargs=inputs,
        return_type=ResearchResult[CampaignData],
    )


def _finish():
    return "finish", {
        "result": {"outcome": "insufficient_evidence", "unknowns": ["No invitation verified"]}
    }


def test_strategy_rejects_code_and_guessed_urls_before_network(monkeypatch):
    calls = redirect_transport(monkeypatch, None)
    runtime = _Runtime(
        [
            ("execute_python", {"code": "import httpx; await httpx.get('https://example.com')"}),
            ("fetch", {"url": "https://guessed.example/jobs"}),
            ("fetch", "invalid JSON"),
            _finish(),
        ]
    )
    result = asyncio.run(
        RetrievalStrategy(ResearchConfig(), retrieval=RetrievalService()).execute(runtime, _call())
    )
    assert result.outcome == "insufficient_evidence"
    assert not calls
    assert {tool.name for tool in runtime.requests[0]["tools"]} == {"search", "fetch", "finish"}


def test_saved_candidate_survives_later_attempt_limit(monkeypatch, search_cache):
    monkeypatch.setattr("malg.core.web_search.httpx.AsyncClient", _FakeHTTPClient)
    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {
        "results": [{"title": "Observed company", "url": "https://observed.example/"}]
    }
    discoveries = []
    runtime = _Runtime(
        [
            ("search", {"query": "software freelancer"}),
            (
                "save_candidate",
                {
                    "display_name": "Observed company",
                    "official_website": "https://observed.example/",
                },
            ),
            ("search", {"query": "  SOFTWARE  Freelancer "}),
            ("search", {"query": "software freelancer"}),
        ]
    )
    strategy = RetrievalStrategy(
        replace(ResearchConfig(), max_no_progress_turns=2),
        retrieval=RetrievalService(SearxngSearchClient(_config(), cache=search_cache)),
        checkpoint=discoveries.append,
    )
    with pytest.raises(BudgetExhausted) as error:
        asyncio.run(strategy.execute(runtime, _call()))
    assert error.value.reason_code == "no_progress_stage_limit"
    assert len(_FakeHTTPClient.calls) == 1
    assert discoveries[0].display_name == "Observed company"


def test_strategy_follows_observed_links_without_guessing(monkeypatch):
    calls = redirect_transport(
        monkeypatch, None, body=b'<a href="/freelancers">Join our freelancer pool</a>'
    )
    runtime = _Runtime(
        [
            ("fetch", {"url": "http://public.example/"}),
            ("fetch", {"url": "http://public.example/freelancers"}),
            _finish(),
        ]
    )
    asyncio.run(
        RetrievalStrategy(ResearchConfig(), retrieval=RetrievalService()).execute(
            runtime, _call(website="http://public.example/")
        )
    )
    assert calls == ["http://public.example/", "http://public.example/freelancers"]


@pytest.mark.parametrize(
    ("errors", "expected"), [([], "healthy"), ([["brave", "CAPTCHA"]], "degraded")]
)
def test_search_health_preserves_partial_results(monkeypatch, errors, expected, search_cache):
    monkeypatch.setattr("malg.core.web_search.httpx.AsyncClient", _FakeHTTPClient)
    _FakeHTTPClient.payload = {
        "results": [{"url": "https://observed.example/"}],
        "unresponsive_engines": errors,
    }
    response = asyncio.run(
        RetrievalService(SearxngSearchClient(_config(), cache=search_cache)).search("software")
    )
    assert response.results
    assert response.health == expected
    assert bool(response.engine_errors) == bool(errors)


def test_search_outage_allows_fetching_urls_known_before_discovery(monkeypatch, search_cache):
    monkeypatch.setattr("malg.core.web_search.httpx.AsyncClient", _FakeHTTPClient)
    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {"results": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]}
    calls = redirect_transport(monkeypatch, None, body=b"Known public evidence")
    url = "http://public.example/"
    runtime = _Runtime(
        [
            ("search", {"query": "software"}),
            ("fetch", {"url": url}),
            _finish(),
        ]
    )
    with pytest.raises(SearchUnavailable) as error:
        asyncio.run(
            RetrievalStrategy(
                ResearchConfig(),
                retrieval=RetrievalService(
                    SearxngSearchClient(_config(), cache=search_cache)
                ),
            ).execute(runtime, _call(website=url))
        )
    assert error.value.reason_code == "search_captcha"
    assert len(_FakeHTTPClient.calls) == 1
    assert calls == [url]
    assert len(runtime.requests) == 3


def test_outage_blocks_new_searches_in_same_response_and_later_turns(monkeypatch, search_cache):
    monkeypatch.setattr("malg.core.web_search.httpx.AsyncClient", _FakeHTTPClient)
    _FakeHTTPClient.calls = []
    _FakeHTTPClient.payload = {"results": [], "unresponsive_engines": [["engine", "CAPTCHA"]]}
    calls = redirect_transport(monkeypatch, None, body=b"Known public evidence")
    url = "http://public.example/"
    runtime = _Runtime(
        [
            [("search", {"query": "first"}), ("search", {"query": "second"})],
            ("search", {"query": "third"}),
            ("fetch", {"url": url}),
            _finish(),
        ]
    )
    with pytest.raises(SearchUnavailable) as error:
        asyncio.run(
            RetrievalStrategy(
                replace(ResearchConfig(), max_no_progress_turns=5),
                retrieval=RetrievalService(
                    SearxngSearchClient(_config(), cache=search_cache)
                ),
            ).execute(runtime, _call(website=url))
        )
    assert error.value.reason_code == "search_captcha"
    assert len(_FakeHTTPClient.calls) == 1
    assert calls == [url]
    assert all(
        "search" not in {tool.name for tool in request["tools"]} for request in runtime.requests[1:]
    )


def test_context_limit_stops_before_request():
    runtime = _Runtime([_finish()])
    with pytest.raises(BudgetExhausted) as error:
        asyncio.run(
            RetrievalStrategy(replace(ResearchConfig(), max_research_context_chars=50)).execute(
                runtime, _call(notes="a" * 100)
            )
        )
    assert error.value.reason_code == "context_stage_limit"
    assert not runtime.requests


def test_nooa_runtime_executes_native_tools_with_offline_sdk(monkeypatch):
    """Verify the real NOOA strategy interface and message rendering without generation."""
    from nooa import Agent
    from nooa.decorators import strategy
    from test_openai_llm import _AsyncClient, _response

    from malg.core.openai_llm import OpenAIChatClient

    monkeypatch.setattr("nooa.agent._try_auto_enable_tracing", lambda: None)

    class ResearchAgent(Agent):
        @strategy(RetrievalStrategy(ResearchConfig(), retrieval=RetrievalService()))
        async def research(self, website: str) -> ResearchResult[CampaignData]:
            """Research only through host tools and return supported results."""
            ...

    calls = redirect_transport(monkeypatch, None)
    messages = [
        SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    id="fetch-1",
                    type="function",
                    function=SimpleNamespace(
                        name="fetch", arguments=json.dumps({"url": "http://public.example/"})
                    ),
                )
            ],
        ),
        SimpleNamespace(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    id="finish-1",
                    type="function",
                    function=SimpleNamespace(name="finish", arguments=json.dumps(_finish()[1])),
                )
            ],
        ),
    ]
    client = _AsyncClient([_response(message, "tool_calls") for message in messages])
    llm = OpenAIChatClient(
        model="offline",
        api_base="https://unused.example/v1",
        api_key="offline",
        context_window=32000,
        max_tokens=4096,
        request_timeout_seconds=10,
        async_client=client,
    )
    result = asyncio.run(ResearchAgent(llm=llm).research("http://public.example/"))
    assert result.outcome == "insufficient_evidence"
    assert calls == ["http://public.example/"]
    request = client.completions.create_request
    assert {tool["function"]["name"] for tool in request["tools"]} == {"search", "fetch", "finish"}
    assert any(
        message["role"] == "tool" and "Observed public source" in message["content"]
        for message in request["messages"]
    )
