"""Async, cached access to Eurostat data for NOOA agents."""

from __future__ import annotations

import asyncio
import copy
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import Any, Callable

import eurostat
import pandas as pd
from nooa import Agent

from malg.config import get_eurostat_config, load_settings


class EurostatSupport(Agent):
    """Base agent with curated, asynchronous and cached Eurostat operations."""

    _cache: OrderedDict[tuple[Any, ...], tuple[float, Any]] = OrderedDict()
    _cache_lock = threading.RLock()
    _request_lock = threading.RLock()
    cache_ttl_seconds = 15 * 60
    cache_max_entries = 32

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.eurostat_config = get_eurostat_config(load_settings())

    async def get_toc(self, *, agency: str | Sequence[str] = "all", lang: str = "en") -> pd.DataFrame:
        """Return Eurostat's table of contents as a DataFrame."""
        return await self._cached_call("toc", eurostat.get_toc_df, agency=agency, lang=lang)

    async def find_datasets(self, keyword: str, *, agency: str | Sequence[str] = "all", lang: str = "en") -> pd.DataFrame:
        """Find datasets whose titles contain ``keyword``."""
        toc = await self.get_toc(agency=agency, lang=lang)
        return eurostat.subset_toc_df(toc, keyword).copy()

    async def get_parameters(self, code: str) -> list[str]:
        """Return the filterable dimensions for a dataset."""
        return await self._cached_call("parameters", eurostat.get_pars, code)

    async def get_parameter_values(self, code: str, parameter: str) -> list[str]:
        """Return valid values for one dataset dimension."""
        return await self._cached_call("parameter_values", eurostat.get_par_values, code, parameter)

    async def get_dictionary(self, code: str, parameter: str | None = None, *, full: bool = True, lang: str = "en") -> pd.DataFrame:
        """Return dataset dimension metadata as a DataFrame."""
        return await self._cached_call("dictionary", eurostat.get_dic, code, parameter, full, "df", lang)

    async def get_data_frame(
        self,
        code: str,
        *,
        flags: bool = False,
        filter_pars: Mapping[str, Any] | None = None,
        verbose: bool = False,
        reverse_time: bool = False,
    ) -> pd.DataFrame:
        """Download a dataset subset as a pandas DataFrame."""
        return await self._cached_call(
            "data_frame",
            eurostat.get_data_df,
            code,
            flags,
            filter_pars=dict(filter_pars) if filter_pars is not None else None,
            verbose=verbose,
            reverse_time=reverse_time,
        )

    @classmethod
    def invalidate_eurostat_cache(cls) -> None:
        """Clear all cached Eurostat responses."""
        with cls._cache_lock:
            cls._cache.clear()

    async def _cached_call(self, name: str, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        key = (name, _freeze(args), _freeze(kwargs), self.eurostat_config)
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None and now - cached[0] < self.cache_ttl_seconds:
                self._cache.move_to_end(key)
                return _copy_result(cached[1])
            if cached is not None:
                del self._cache[key]

        result = await asyncio.to_thread(self._run_request, function, args, kwargs)
        with self._cache_lock:
            self._cache[key] = (time.monotonic(), result)
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_max_entries:
                self._cache.popitem(last=False)
        return _copy_result(result)

    def _run_request(self, function: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        """Apply global Eurostat HTTP settings and execute one request safely."""
        config = self.eurostat_config
        proxies = {"https": config.proxy} if config.proxy else None
        with self._request_lock:
            eurostat.set_requests_args(timeout=config.timeout_seconds, proxies=proxies, verify=config.verify, cert=config.cert)
            return function(*args, **kwargs)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze(item) for item in value)
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _copy_result(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return value.copy(deep=True)
    return copy.deepcopy(value)
