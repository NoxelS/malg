from __future__ import annotations

import asyncio

import pandas as pd

from malg.config import EurostatConfig
from malg.core import eurostat_support
from malg.core.eurostat_support import EurostatSupport


def _support() -> EurostatSupport:
    support = object.__new__(EurostatSupport)
    support.eurostat_config = EurostatConfig(
        timeout_seconds=120, proxy=None, verify=True, cert=None
    )
    return support


def test_get_data_frame_is_async_cached_and_returns_copy(monkeypatch) -> None:
    calls = 0

    def fake_get_data_df(*args, **kwargs):
        nonlocal calls
        calls += 1
        return pd.DataFrame({"geo": ["IT"], "2025": [42]})

    monkeypatch.setattr(eurostat_support.eurostat, "get_data_df", fake_get_data_df)
    monkeypatch.setattr(eurostat_support.eurostat, "set_requests_args", lambda **kwargs: None)
    EurostatSupport.invalidate_eurostat_cache()
    support = _support()

    async def run() -> tuple[pd.DataFrame, pd.DataFrame]:
        first = await support.get_data_frame("demo", filter_pars={"geo": ["IT"]})
        first.loc[0, "2025"] = 0
        second = await support.get_data_frame("demo", filter_pars={"geo": ["IT"]})
        return first, second

    _, second = asyncio.run(run())

    assert calls == 1
    assert second.loc[0, "2025"] == 42


def test_cache_invalidation_forces_new_request(monkeypatch) -> None:
    calls = 0

    def fake_get_pars(code: str) -> list[str]:
        nonlocal calls
        calls += 1
        return [code]

    monkeypatch.setattr(eurostat_support.eurostat, "get_pars", fake_get_pars)
    monkeypatch.setattr(eurostat_support.eurostat, "set_requests_args", lambda **kwargs: None)
    EurostatSupport.invalidate_eurostat_cache()
    support = _support()

    async def run() -> None:
        await support.get_parameters("one")
        await support.get_parameters("one")
        support.invalidate_eurostat_cache()
        await support.get_parameters("one")

    asyncio.run(run())

    assert calls == 2
