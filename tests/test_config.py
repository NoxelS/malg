from __future__ import annotations

from pathlib import Path

from malg.config import get_llm_config, load_settings


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
