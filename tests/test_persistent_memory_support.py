"""Observable tests for scoped durable NOOA research memory."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from malg.core.persistent_memory_support import PersistentMemorySupport, close_persistent_memory
from malg.utils.console_progress import ConsoleProgress
from malg.utils.decorators import use_default_llm_endpoint


@use_default_llm_endpoint()
class CampaignMemoryAgent(PersistentMemorySupport):
    """Minimal agent type used to test campaign-memory persistence."""

    memory_scope = "campaign-research"


@use_default_llm_endpoint()
class IsolatedMemoryAgent(PersistentMemorySupport):
    """Minimal second agent type used to test scoped-memory isolation."""

    memory_scope = "secondary-research"


def test_memory_persists_for_fresh_agent_of_same_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MALG_MEMORY_DIRECTORY", str(tmp_path))
    first = CampaignMemoryAgent()
    try:
        memory_id = first.remember_source(
            "Eurostat AI-use data is a campaign-selection signal.",
            "https://ec.europa.eu/eurostat/",
            dataset_code="isoc_eb_ai",
            tags=["eurostat", "ai"],
        )
    finally:
        close_persistent_memory(first)

    second = CampaignMemoryAgent()
    try:
        recalled = second.search("Eurostat AI-use", k=5)
        assert [memory.id for memory in recalled] == [memory_id]
        assert "Dataset: isoc_eb_ai" in recalled[0].content
    finally:
        close_persistent_memory(second)


def test_memory_is_isolated_by_agent_type(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MALG_MEMORY_DIRECTORY", str(tmp_path))
    campaign = CampaignMemoryAgent()
    secondary = IsolatedMemoryAgent()
    try:
        campaign.remember("Campaign-only source finding", tags=["campaign"])
        assert secondary.search("Campaign-only source finding") == []
        assert (tmp_path / "campaign-research.sqlite").exists()
        assert (tmp_path / "secondary-research.sqlite").exists()
    finally:
        close_persistent_memory(campaign)
        close_persistent_memory(secondary)


def test_remember_source_requires_absolute_http_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MALG_MEMORY_DIRECTORY", str(tmp_path))
    agent = CampaignMemoryAgent()
    try:
        with pytest.raises(ValueError, match="absolute HTTP"):
            agent.remember_source("A finding", "not-a-url")
    finally:
        close_persistent_memory(agent)


def test_memory_progress_reports_operations_without_memory_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MALG_MEMORY_DIRECTORY", str(tmp_path))
    output = StringIO()
    agent = CampaignMemoryAgent()
    try:
        progress = ConsoleProgress(stream=output, timestamp=lambda: "12:34:56", color=False)
        progress.attach(agent)
        memory_id = agent.remember("Sensitive finding body", tags=["private"])
        assert len(agent.search("Sensitive finding body")) == 1
        assert agent.update_memory(memory_id, tags=["updated"])
        assert agent.forget(memory_id)
    finally:
        close_persistent_memory(agent)

    rendered = output.getvalue()
    assert "campaign-research saved" in rendered
    assert "campaign-research search loaded 1 item" in rendered
    assert "campaign-research updated" in rendered
    assert "campaign-research archived" in rendered
    assert "Sensitive finding body" not in rendered
    assert "private" not in rendered
