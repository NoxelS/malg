"""Application configuration loaded from files and environment variables."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dynaconf import Dynaconf

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_FILES = (PROJECT_ROOT / "default.config.toml",)


@dataclass(frozen=True)
class LLMConfig:
    """Configuration needed to construct MALG's OpenAI-compatible client."""

    model: str
    api_base: str
    api_key: str | None
    context_window: int | None
    max_tokens: int | None
    headroom_compression: bool
    enable_thinking: bool | None
    parallel_tool_calls: bool
    request_timeout_seconds: int
    max_retries: int = 3


@dataclass(frozen=True)
class BrowserConfig:
    """Configuration for the Lightpanda MCP endpoint."""

    enabled: bool
    url: str
    timeout_seconds: int


@dataclass(frozen=True)
class SearchConfig:
    """Bounded SearXNG discovery settings for browser-capable agents."""

    enabled: bool
    url: str
    timeout_seconds: int
    max_results: int
    max_requests_per_run: int
    min_interval_seconds: float
    languages: tuple[str, ...]
    categories: tuple[str, ...]


@dataclass(frozen=True)
class EurostatConfig:
    """HTTP request settings for the Eurostat client."""

    timeout_seconds: float
    proxy: str | None
    verify: bool | str | None
    cert: str | None


@dataclass(frozen=True)
class ICPConfig:
    """Host-owned limits for ICP batch research."""

    batch_size: int
    concurrency: int
    max_attempts_per_slot: int
    max_exclusion_cards: int


@dataclass(frozen=True)
class WorkerConfig:
    """Lease and polling settings for one durable worker process."""

    poll_interval_seconds: int = 1
    lease_seconds: int = 7200
    max_attempts: int = 3
    heartbeat_timeout_seconds: int = 15
@dataclass(frozen=True)
class ResearchConfig:
    """Finite host-owned limits shared by every research workflow stage."""

    workflow_timeout_seconds: int = 600
    stage_timeout_seconds: int = 180
    qualification_timeout_seconds: int = 180
    project_timeout_seconds: int = 120
    contact_timeout_seconds: int = 180
    review_timeout_seconds: int = 90
    llm_attempt_timeout_seconds: int = 90
    initialization_timeout_seconds: int = 20
    cleanup_reserve_seconds: int = 5
    max_iterations: int = 6
    max_llm_attempts_per_stage: int = 12
    max_llm_attempts_per_workflow: int = 24
    max_search_requests: int = 12
    max_fetch_requests: int = 20
    discovery_batch_size: int = 10
    max_discovery_candidates: int = 20
    extraction_enable_thinking: bool = False
    synthesis_enable_thinking: bool = False
    thinking_supported: bool = False


def get_research_config(settings: Dynaconf) -> ResearchConfig:
    """Read and validate finite research limits from ``[default.research]``."""
    research = settings.get("research")
    if not isinstance(research, Mapping):
        raise ValueError("Missing [default.research] configuration.")
    defaults = ResearchConfig()
    values: dict[str, object] = {}
    integer_fields = (
        "workflow_timeout_seconds", "stage_timeout_seconds",
        "qualification_timeout_seconds", "project_timeout_seconds",
        "contact_timeout_seconds", "review_timeout_seconds",
        "llm_attempt_timeout_seconds", "initialization_timeout_seconds",
        "cleanup_reserve_seconds", "max_iterations",
        "max_llm_attempts_per_stage", "max_llm_attempts_per_workflow",
        "max_search_requests", "max_fetch_requests", "discovery_batch_size",
        "max_discovery_candidates",
    )
    for field in integer_fields:
        value = research.get(field, getattr(defaults, field))
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"Research configuration field {field} must be positive.")
        values[field] = value
    for field in ("extraction_enable_thinking", "synthesis_enable_thinking", "thinking_supported"):
        value = research.get(field, getattr(defaults, field))
        if not isinstance(value, bool):
            raise ValueError(f"Research configuration field {field} must be boolean.")
        values[field] = value
    reserve = values["cleanup_reserve_seconds"]
    for field in (
        "workflow_timeout_seconds", "stage_timeout_seconds",
        "qualification_timeout_seconds", "project_timeout_seconds",
        "contact_timeout_seconds", "review_timeout_seconds",
        "llm_attempt_timeout_seconds", "initialization_timeout_seconds",
    ):
        if reserve >= values[field]:
            raise ValueError(f"cleanup_reserve_seconds must be less than {field}.")
    return ResearchConfig(**values)


@dataclass(frozen=True)
class AuthConfig:
    """Configuration for MALG's single administrator-equivalent account."""

    username: str
    password: str


def get_auth_config(settings: Dynaconf) -> AuthConfig:
    """Read authentication credentials, allowing blank password disablement."""
    auth = settings.get("auth")
    if not isinstance(auth, Mapping):
        raise ValueError("Missing [default.auth] configuration.")
    username = auth.get("username")
    password = auth.get("password")
    if not isinstance(username, str):
        raise ValueError("Auth configuration field username must be a string.")
    if not username:
        raise ValueError("Auth configuration field username must be non-empty.")
    if not isinstance(password, str):
        raise ValueError("Auth configuration field password must be a string.")
    return AuthConfig(username=username, password=password)


def get_worker_config(settings: Dynaconf) -> WorkerConfig:
    """Read and validate positive worker lease and liveness settings."""
    worker = settings.get("worker")
    if not isinstance(worker, Mapping):
        raise ValueError("Missing [default.worker] configuration.")
    invalid = [
        field
        for field in (
            "poll_interval_seconds",
            "lease_seconds",
            "max_attempts",
            "heartbeat_timeout_seconds",
        )
        if not isinstance(worker.get(field), int)
        or isinstance(worker[field], bool)
        or worker[field] <= 0
    ]
    if invalid:
        raise ValueError(
            f"Worker configuration field(s) must be positive integers: {', '.join(invalid)}."
        )
    if worker["heartbeat_timeout_seconds"] < 2 * worker["poll_interval_seconds"]:
        raise ValueError("heartbeat_timeout_seconds must be at least twice poll_interval_seconds.")
    return WorkerConfig(
        poll_interval_seconds=worker["poll_interval_seconds"],
        lease_seconds=worker["lease_seconds"],
        max_attempts=worker["max_attempts"],
        heartbeat_timeout_seconds=worker["heartbeat_timeout_seconds"],
    )


def load_settings(
    *,
    settings_files: Sequence[str | Path] = DEFAULT_CONFIG_FILES,
    load_dotenv: bool = True,
) -> Dynaconf:
    """Load default config, then .env, then exported environment variables."""
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

    required_fields = ("model", "api_base")
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

    headroom_compression = llm.get("headroom_compression", False)
    if not isinstance(headroom_compression, bool):
        raise ValueError("LLM configuration field headroom_compression must be a boolean.")

    enable_thinking = llm.get("enable_thinking")
    if enable_thinking is not None and not isinstance(enable_thinking, bool):
        raise ValueError("LLM configuration field enable_thinking must be a boolean when set.")

    parallel_tool_calls = llm.get("parallel_tool_calls", False)
    if not isinstance(parallel_tool_calls, bool):
        raise ValueError("LLM configuration field parallel_tool_calls must be a boolean.")

    request_timeout_seconds = llm.get("request_timeout_seconds")
    if (
        not isinstance(request_timeout_seconds, int)
        or isinstance(request_timeout_seconds, bool)
        or request_timeout_seconds <= 0
    ):
        raise ValueError(
            "LLM configuration field request_timeout_seconds must be a positive integer."
        )

    max_retries = llm.get("max_retries", 3)
    if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
        raise ValueError("LLM configuration field max_retries must be a non-negative integer.")

    return LLMConfig(
        model=llm["model"],
        api_base=llm["api_base"],
        api_key=api_key,
        context_window=llm.get("context_window"),
        max_tokens=llm.get("max_tokens"),
        headroom_compression=headroom_compression,
        enable_thinking=enable_thinking,
        parallel_tool_calls=parallel_tool_calls,
        request_timeout_seconds=request_timeout_seconds,
        max_retries=max_retries,
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


def get_search_config(settings: Dynaconf) -> SearchConfig:
    """Read bounded SearXNG discovery settings for browser-capable agents."""
    search = settings.get("search")
    if not isinstance(search, Mapping):
        raise ValueError("Missing [default.search] configuration.")

    enabled = search.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("Search configuration field enabled must be a boolean.")

    url = search.get("url")
    if not isinstance(url, str):
        raise ValueError("Search configuration field url must be a string.")
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("Search configuration field url must be an absolute HTTP(S) URL.")

    integer_fields = ("timeout_seconds", "max_results", "max_requests_per_run")
    invalid_integer_fields = [
        field
        for field in integer_fields
        if not isinstance(search.get(field), int) or search[field] <= 0
    ]
    if invalid_integer_fields:
        names = ", ".join(invalid_integer_fields)
        raise ValueError(f"Search configuration field(s) must be positive integers: {names}.")

    min_interval_seconds = search.get("min_interval_seconds")
    if (
        not isinstance(min_interval_seconds, (int, float))
        or isinstance(min_interval_seconds, bool)
        or min_interval_seconds < 0
        or min_interval_seconds > 60
    ):
        raise ValueError(
            "Search configuration field min_interval_seconds must be between 0 and 60."
        )

    list_fields = ("languages", "categories")
    invalid_list_fields = [
        field
        for field in list_fields
        if not isinstance(search.get(field), list)
        or not search[field]
        or not all(isinstance(value, str) and value for value in search[field])
    ]
    if invalid_list_fields:
        names = ", ".join(invalid_list_fields)
        raise ValueError(f"Search configuration field(s) must be non-empty string lists: {names}.")

    return SearchConfig(
        enabled=enabled,
        url=url.rstrip("/"),
        timeout_seconds=search["timeout_seconds"],
        max_results=search["max_results"],
        max_requests_per_run=search["max_requests_per_run"],
        min_interval_seconds=float(min_interval_seconds),
        languages=tuple(search["languages"]),
        categories=tuple(search["categories"]),
    )


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


def get_icp_config(settings: Dynaconf) -> ICPConfig:
    """Read bounded ICP batch settings without accepting agent-provided limits."""
    icp = settings.get("icp")
    if not isinstance(icp, Mapping):
        raise ValueError("Missing [default.icp] configuration.")

    integer_fields = ("batch_size", "concurrency", "max_attempts_per_slot", "max_exclusion_cards")
    invalid_fields = [
        field
        for field in integer_fields
        if not isinstance(icp.get(field), int) or isinstance(icp[field], bool) or icp[field] <= 0
    ]
    if invalid_fields:
        names = ", ".join(invalid_fields)
        raise ValueError(f"ICP configuration field(s) must be positive integers: {names}.")

    return ICPConfig(
        batch_size=icp["batch_size"],
        concurrency=icp["concurrency"],
        max_attempts_per_slot=icp["max_attempts_per_slot"],
        max_exclusion_cards=icp["max_exclusion_cards"],
    )
