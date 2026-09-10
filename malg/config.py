"""Application configuration loaded from files and environment variables."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dynaconf import Dynaconf

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_FILES = (
    PROJECT_ROOT / "default.config.toml",
    PROJECT_ROOT / "user.config.toml",
)


@dataclass(frozen=True)
class LLMConfig:
    """Configuration needed to construct the NOOA LiteLLM client."""

    model: str
    provider: str
    api_base: str
    api_key: str | None
    context_window: int | None
    max_tokens: int | None


@dataclass(frozen=True)
class BrowserConfig:
    """Configuration for the Lightpanda MCP endpoint."""

    enabled: bool
    url: str
    timeout_seconds: int


@dataclass(frozen=True)
class EurostatConfig:
    """HTTP request settings for the Eurostat client."""

    timeout_seconds: float
    proxy: str | None
    verify: bool | str | None
    cert: str | None


def load_settings(
    *,
    settings_files: Sequence[str | Path] = DEFAULT_CONFIG_FILES,
    load_dotenv: bool = True,
) -> Dynaconf:
    """Load defaults, optional user overrides, then .env and environment overrides."""
    return Dynaconf(
        settings_files=[str(path) for path in settings_files],
        environments=True,
        envvar_prefix="MALG",
        load_dotenv=load_dotenv,
        merge_enabled=True,
    )


def get_llm_config(settings: Dynaconf) -> LLMConfig:
    """Read the configured LLM fields with a small, explicit boundary."""
    llm = settings.get("llm")
    if not isinstance(llm, Mapping):
        raise ValueError("Missing [default.llm] configuration.")

    required_fields = ("model", "provider", "api_base")
    invalid_fields = [field for field in required_fields if not isinstance(llm.get(field), str)]
    if invalid_fields:
        names = ", ".join(invalid_fields)
        raise ValueError(f"LLM configuration requires string field(s): {names}.")

    api_key = llm.get("api_key")
    if api_key is not None and not isinstance(api_key, str):
        raise ValueError("LLM configuration field api_key must be a string when set.")

    optional_integer_fields = ("context_window", "max_tokens")
    invalid_integer_fields = [
        field
        for field in optional_integer_fields
        if llm.get(field) is not None and (not isinstance(llm.get(field), int) or llm[field] <= 0)
    ]
    if invalid_integer_fields:
        names = ", ".join(invalid_integer_fields)
        raise ValueError(f"LLM configuration field(s) must be positive integers: {names}.")

    return LLMConfig(
        model=llm["model"],
        provider=llm["provider"],
        api_base=llm["api_base"],
        api_key=api_key,
        context_window=llm.get("context_window"),
        max_tokens=llm.get("max_tokens"),
    )


def get_browser_config(settings: Dynaconf) -> BrowserConfig:
    """Read the configured Lightpanda MCP endpoint."""
    browser = settings.get("browser")
    if not isinstance(browser, Mapping):
        raise ValueError("Missing [default.browser] configuration.")

    enabled = browser.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("Browser configuration field enabled must be a boolean.")

    url = browser.get("url")
    if not isinstance(url, str):
        raise ValueError("Browser configuration field url must be a string.")
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("Browser configuration field url must be an absolute HTTP(S) URL.")

    timeout_seconds = browser.get("timeout_seconds")
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise ValueError("Browser configuration field timeout_seconds must be a positive integer.")

    return BrowserConfig(enabled=enabled, url=url, timeout_seconds=timeout_seconds)


def get_eurostat_config(settings: Dynaconf) -> EurostatConfig:
    """Read and validate the configured Eurostat request settings."""
    eurostat = settings.get("eurostat")
    if not isinstance(eurostat, Mapping):
        raise ValueError("Missing [default.eurostat] configuration.")

    timeout_seconds = eurostat.get("timeout_seconds")
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        raise ValueError("Eurostat configuration field timeout_seconds must be positive.")

    proxy = eurostat.get("proxy")
    if proxy == "":
        proxy = None
    if proxy is not None and not isinstance(proxy, str):
        raise ValueError("Eurostat configuration field proxy must be a string when set.")

    verify = eurostat.get("verify")
    if verify == "":
        verify = None
    if verify is not None and not isinstance(verify, (bool, str)):
        raise ValueError(
            "Eurostat configuration field verify must be a boolean or string when set."
        )

    cert = eurostat.get("cert")
    if cert == "":
        cert = None
    if cert is not None and not isinstance(cert, str):
        raise ValueError("Eurostat configuration field cert must be a string when set.")

    return EurostatConfig(timeout_seconds=timeout_seconds, proxy=proxy, verify=verify, cert=cert)
