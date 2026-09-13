from __future__ import annotations

import os
from pathlib import Path

import pytest

from malg.config import (
    AuthConfig,
    get_auth_config,
    get_browser_config,
    get_eurostat_config,
    get_icp_config,
    get_llm_config,
    get_search_config,
    load_settings,
)


@pytest.fixture(autouse=True)
def clear_environment_dotenv_values() -> None:
    """Keep repository-local dotenv values from leaking between config tests."""
    for key in tuple(os.environ):
        if key.startswith("MALG_"):
            os.environ.pop(key)


def test_auth_config_defaults_and_environment_overrides(tmp_path: Path, monkeypatch) -> None:
    config_file = tmp_path / "default.config.toml"
    config_file.write_text("[default.auth]\nusername = 'admin'\npassword = ''\n")
    monkeypatch.setenv("MALG_AUTH__USERNAME", "operator")
    monkeypatch.setenv("MALG_AUTH__PASSWORD", "secret")
    config = get_auth_config(load_settings(settings_files=(config_file,), load_dotenv=False))
    assert config.username == "operator"
    assert config.password == "secret"


def test_auth_config_blank_password_disables_authentication(tmp_path: Path) -> None:
    config_file = tmp_path / "default.config.toml"
    config_file.write_text("[default.auth]\nusername = 'admin'\npassword = ''\n")
    config = get_auth_config(load_settings(settings_files=(config_file,), load_dotenv=False))
    assert config == AuthConfig(username="admin", password="")


@pytest.mark.parametrize("field", ["username", "password"])
def test_auth_config_rejects_invalid_field_types(tmp_path: Path, field: str) -> None:
    config_file = tmp_path / "default.config.toml"
    config_file.write_text(
        "[default.auth]\nusername = 1\npassword = ''\n"
        if field == "username"
        else "[default.auth]\nusername = 'admin'\npassword = 1\n"
    )
    with pytest.raises(ValueError, match=field):
        get_auth_config(load_settings(settings_files=(config_file,), load_dotenv=False))


def test_dotenv_overrides_default(tmp_path: Path, monkeypatch) -> None:
    default_config = tmp_path / "default.config.toml"
    default_config.write_text(
        "[default.llm]\nmodel = 'default-model'\napi_base = 'https://default.example/v1'\n"
    )
    (tmp_path / ".env").write_text(
        "MALG_LLM__MODEL=dotenv-model\n"
        "MALG_LLM__API_KEY=dotenv-key\n"
        "MALG_LLM__ENABLE_THINKING=false\n"
        "MALG_LLM__CONTEXT_WINDOW=32768\n"
        "MALG_LLM__MAX_TOKENS=2048\n"
        "MALG_LLM__HEADROOM_COMPRESSION=true\n"
        "MALG_LLM__REQUEST_TIMEOUT_SECONDS=300\n"
        "MALG_LLM__PARALLEL_TOOL_CALLS=true\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOTENV_PATH_FOR_DYNACONF", str(tmp_path / ".env"))
    monkeypatch.setenv("DOTENV_OVERRIDE_FOR_DYNACONF", "true")
    config = get_llm_config(load_settings(settings_files=(default_config,), load_dotenv=True))
    for key in (
        "MALG_LLM__MODEL",
        "MALG_LLM__API_KEY",
        "MALG_LLM__ENABLE_THINKING",
        "MALG_LLM__CONTEXT_WINDOW",
        "MALG_LLM__MAX_TOKENS",
        "MALG_LLM__HEADROOM_COMPRESSION",
        "MALG_LLM__REQUEST_TIMEOUT_SECONDS",
        "MALG_LLM__PARALLEL_TOOL_CALLS",
    ):
        os.environ.pop(key, None)
    os.environ.pop("DOTENV_OVERRIDE_FOR_DYNACONF", None)

    assert config.model == "dotenv-model"
    assert config.api_base == "https://default.example/v1"
    assert config.api_key == "dotenv-key"
    assert config.context_window == 32768
    assert config.max_tokens == 2048
    assert config.headroom_compression is True
    assert config.enable_thinking is False
    assert config.parallel_tool_calls is True
    assert config.request_timeout_seconds == 300
    assert config.max_retries == 3


def test_environment_overrides_config_files(tmp_path: Path, monkeypatch) -> None:
    default_config = tmp_path / "default.config.toml"
    default_config.write_text(
        "[default.llm]\nmodel = 'default-model'\napi_base = 'https://default.example/v1'\nrequest_timeout_seconds = 300\n"
    )
    monkeypatch.setenv("MALG_LLM__MODEL", "environment-model")
    monkeypatch.setenv("MALG_LLM__API_KEY", "environment-key")

    config = get_llm_config(load_settings(settings_files=(default_config,), load_dotenv=False))

    assert config.model == "environment-model"
    assert config.api_key == "environment-key"
    assert config.context_window is None
    assert config.max_tokens is None
    assert config.headroom_compression is False
    assert config.enable_thinking is None
    assert config.parallel_tool_calls is False
    assert config.request_timeout_seconds == 300
    assert config.max_retries == 3


def test_llm_config_rejects_invalid_request_timeout(tmp_path: Path) -> None:
    config_file = tmp_path / "default.config.toml"
    config_file.write_text(
        "[default.llm]\nmodel = 'model'\napi_base = 'https://example.test/v1'\n"
        "request_timeout_seconds = 0\n"
    )

    with pytest.raises(ValueError, match="request_timeout_seconds"):
        get_llm_config(load_settings(settings_files=(config_file,), load_dotenv=False))


def test_llm_config_rejects_negative_max_retries(tmp_path: Path) -> None:
    config_file = tmp_path / "default.config.toml"
    config_file.write_text(
        "[default.llm]\nmodel = 'model'\napi_base = 'https://example.test/v1'\n"
        "request_timeout_seconds = 300\nmax_retries = -1\n"
    )

    with pytest.raises(ValueError, match="max_retries"):
        get_llm_config(load_settings(settings_files=(config_file,), load_dotenv=False))


def test_llm_config_rejects_invalid_enable_thinking(tmp_path: Path) -> None:
    config_file = tmp_path / "default.config.toml"
    config_file.write_text(
        "[default.llm]\nmodel = 'model'\napi_base = 'https://example.test/v1'\n"
        "request_timeout_seconds = 300\nenable_thinking = 'sometimes'\n"
    )

    with pytest.raises(ValueError, match="enable_thinking"):
        get_llm_config(load_settings(settings_files=(config_file,), load_dotenv=False))


def test_llm_config_rejects_invalid_parallel_tool_calls(tmp_path: Path) -> None:
    config_file = tmp_path / "default.config.toml"
    config_file.write_text(
        "[default.llm]\nmodel = 'model'\napi_base = 'https://example.test/v1'\n"
        "request_timeout_seconds = 300\nparallel_tool_calls = 'sometimes'\n"
    )

    with pytest.raises(ValueError, match="parallel_tool_calls"):
        get_llm_config(load_settings(settings_files=(config_file,), load_dotenv=False))


def test_browser_config_uses_environment_overrides(tmp_path: Path, monkeypatch) -> None:
    default_config = tmp_path / "default.config.toml"
    default_config.write_text(
        "[default.browser]\nenabled = true\nurl = 'http://lightpanda:9223/mcp'\ntimeout_seconds = 60\n"
    )
    monkeypatch.setenv("MALG_BROWSER__URL", "http://localhost:9223/mcp")
    monkeypatch.setenv("MALG_BROWSER__TIMEOUT_SECONDS", "15")

    config = get_browser_config(load_settings(settings_files=(default_config,), load_dotenv=False))

    assert config.enabled is True
    assert config.url == "http://localhost:9223/mcp"
    assert config.timeout_seconds == 15


def test_browser_config_rejects_invalid_endpoint(tmp_path: Path) -> None:
    default_config = tmp_path / "default.config.toml"
    default_config.write_text(
        "[default.browser]\nenabled = true\nurl = 'lightpanda:9223/mcp'\ntimeout_seconds = 60\n"
    )

    with pytest.raises(ValueError, match="absolute HTTP"):
        get_browser_config(load_settings(settings_files=(default_config,), load_dotenv=False))


def test_search_config_uses_environment_overrides(tmp_path: Path, monkeypatch) -> None:
    default_config = tmp_path / "default.config.toml"
    default_config.write_text(
        "[default.search]\nenabled = true\nurl = 'http://searxng:8080'\ntimeout_seconds = 20\n"
        "max_results = 5\nmax_requests_per_run = 8\nmin_interval_seconds = 2\n"
        "languages = ['en', 'de']\ncategories = ['general']\n"
    )
    monkeypatch.setenv("MALG_SEARCH__MAX_REQUESTS_PER_RUN", "3")
    monkeypatch.setenv("MALG_SEARCH__MIN_INTERVAL_SECONDS", "1.5")

    config = get_search_config(load_settings(settings_files=(default_config,), load_dotenv=False))

    assert config.url == "http://searxng:8080"
    assert config.max_requests_per_run == 3
    assert config.min_interval_seconds == 1.5
    assert config.languages == ("en", "de")


def test_search_config_rejects_empty_categories(tmp_path: Path) -> None:
    config_file = tmp_path / "default.config.toml"
    config_file.write_text(
        "[default.search]\nenabled = true\nurl = 'http://searxng:8080'\ntimeout_seconds = 20\n"
        "max_results = 5\nmax_requests_per_run = 8\nmin_interval_seconds = 2\n"
        "languages = ['en']\ncategories = []\n"
    )

    with pytest.raises(ValueError, match="non-empty string lists: categories"):
        get_search_config(load_settings(settings_files=(config_file,), load_dotenv=False))


def test_eurostat_config_reads_request_settings(tmp_path: Path) -> None:
    config_file = tmp_path / "default.config.toml"
    config_file.write_text(
        "[default.eurostat]\ntimeout_seconds = 45\nproxy = 'http://proxy.example:8080'\nverify = '/tmp/ca.pem'\ncert = '/tmp/client.pem'\n"
    )

    config = get_eurostat_config(load_settings(settings_files=(config_file,), load_dotenv=False))

    assert config.timeout_seconds == 45
    assert config.proxy == "http://proxy.example:8080"
    assert config.verify == "/tmp/ca.pem"
    assert config.cert == "/tmp/client.pem"


def test_icp_config_reads_environment_overrides(tmp_path: Path, monkeypatch) -> None:
    config_file = tmp_path / "default.config.toml"
    config_file.write_text(
        "[default.icp]\nbatch_size = 10\nconcurrency = 3\nmax_attempts_per_slot = 3\n"
        "max_exclusion_cards = 100\n"
    )
    monkeypatch.setenv("MALG_ICP__BATCH_SIZE", "4")

    config = get_icp_config(load_settings(settings_files=(config_file,), load_dotenv=False))

    assert config.batch_size == 4
    assert config.concurrency == 3
    assert config.max_attempts_per_slot == 3
    assert config.max_exclusion_cards == 100


def test_icp_config_rejects_non_positive_batch_size(tmp_path: Path) -> None:
    config_file = tmp_path / "default.config.toml"
    config_file.write_text(
        "[default.icp]\nbatch_size = 0\nconcurrency = 3\nmax_attempts_per_slot = 3\n"
        "max_exclusion_cards = 100\n"
    )

    with pytest.raises(ValueError, match="batch_size"):
        get_icp_config(load_settings(settings_files=(config_file,), load_dotenv=False))
