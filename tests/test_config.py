from __future__ import annotations

from pathlib import Path

import pytest

from malg.config import get_browser_config, get_eurostat_config, get_llm_config, load_settings


def test_user_config_overrides_default(tmp_path: Path) -> None:
    default_config = tmp_path / "default.config.toml"
    default_config.write_text("[default.llm]\nmodel = 'default-model'\nprovider = 'openai'\napi_base = 'https://default.example/v1'\n")
    user_config = tmp_path / "user.config.toml"
    user_config.write_text("[default.llm]\nmodel = 'user-model'\napi_key = 'user-key'\ncontext_window = 32768\nmax_tokens = 2048\n")

    config = get_llm_config(load_settings(settings_files=(default_config, user_config), load_dotenv=False))

    assert config.model == "user-model"
    assert config.provider == "openai"
    assert config.api_base == "https://default.example/v1"
    assert config.api_key == "user-key"
    assert config.context_window == 32768
    assert config.max_tokens == 2048


def test_environment_overrides_config_files(tmp_path: Path, monkeypatch) -> None:
    default_config = tmp_path / "default.config.toml"
    default_config.write_text("[default.llm]\nmodel = 'default-model'\nprovider = 'openai'\napi_base = 'https://default.example/v1'\n")
    monkeypatch.setenv("MALG_LLM__MODEL", "environment-model")
    monkeypatch.setenv("MALG_LLM__API_KEY", "environment-key")

    config = get_llm_config(load_settings(settings_files=(default_config,), load_dotenv=False))

    assert config.model == "environment-model"
    assert config.api_key == "environment-key"
    assert config.context_window is None
    assert config.max_tokens is None


def test_browser_config_uses_environment_overrides(tmp_path: Path, monkeypatch) -> None:
    default_config = tmp_path / "default.config.toml"
    default_config.write_text("[default.browser]\nenabled = true\nurl = 'http://lightpanda:9223/mcp'\ntimeout_seconds = 60\n")
    monkeypatch.setenv("MALG_BROWSER__URL", "http://localhost:9223/mcp")
    monkeypatch.setenv("MALG_BROWSER__TIMEOUT_SECONDS", "15")

    config = get_browser_config(load_settings(settings_files=(default_config,), load_dotenv=False))

    assert config.enabled is True
    assert config.url == "http://localhost:9223/mcp"
    assert config.timeout_seconds == 15


def test_browser_config_rejects_invalid_endpoint(tmp_path: Path) -> None:
    default_config = tmp_path / "default.config.toml"
    default_config.write_text("[default.browser]\nenabled = true\nurl = 'lightpanda:9223/mcp'\ntimeout_seconds = 60\n")

    with pytest.raises(ValueError, match="absolute HTTP"):
        get_browser_config(load_settings(settings_files=(default_config,), load_dotenv=False))


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
