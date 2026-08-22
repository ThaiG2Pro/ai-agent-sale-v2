"""Tests for LiteLLM config cloud fast-path (WP5).

Cloud model strings (gemini/*, gpt-*, ...) must NOT be pinned to the local
Ollama api_base — otherwise setting CHAT_MODEL=gemini/gemini-2.5-flash would
route Gemini calls at the Ollama server.
"""

from __future__ import annotations

from core.ai_config import _litellm_params
from core.config import Settings, settings


def test_cloud_models_do_not_get_ollama_api_base(monkeypatch) -> None:
    monkeypatch.setattr("core.config.settings.GEMINI_API_KEY", "sk-test")
    monkeypatch.setattr("core.config.settings.OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("core.config.settings.GROQ_API_KEY", "sk-test")
    for model in ("gemini/gemini-2.5-flash", "gpt-4o-mini", "groq/llama-3.3-70b-versatile"):
        params = _litellm_params(model)
        assert "api_base" not in params, f"{model} must not be pinned to Ollama"
        assert "num_ctx" not in params, f"{model} must not get the Ollama num_ctx option"


def test_extra_kwargs_pass_through() -> None:
    params = _litellm_params("ollama/qwen3-1.7b", stream=True)
    assert params["stream"] is True


def test_empty_env_values_fall_back_to_defaults(monkeypatch) -> None:
    """docker-compose pass-through `${VAR:-}` sends empty strings — the app
    must treat them as unset (env_ignore_empty)."""
    monkeypatch.setenv("CHAT_MODEL", "")
    monkeypatch.setenv("EMBED_MODEL", "")
    monkeypatch.setenv("CACHE_TTL_SECONDS", "")
    s = Settings()
    assert s.CHAT_MODEL != ""
    assert s.EMBED_MODEL != ""
    assert s.CACHE_TTL_SECONDS >= 0


def test_cache_ttl_setting_exists() -> None:
    assert settings.CACHE_TTL_SECONDS >= 0
