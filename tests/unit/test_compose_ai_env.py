"""Static checks on docker-compose.yml AI env passthrough (WP5).

No Docker needed — parses the YAML and asserts the api service forwards the
AI provider configuration and can reach an Ollama server on the host.
"""

from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _api_service() -> dict:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return compose["services"]["api"]


def test_api_service_forwards_ai_env() -> None:
    env = _api_service()["environment"]
    for var in (
        "OLLAMA_BASE_URL",
        "LIGHT_CHAT_MODEL",
        "CHAT_MODEL",
        "POWERFUL_CHAT_MODEL",
        "EMBED_MODEL",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "X_ADMIN_KEY",
        "CACHE_TTL_SECONDS",
    ):
        assert var in env, f"api service missing env passthrough: {var}"


def test_ollama_base_url_defaults_to_host_gateway() -> None:
    env = _api_service()["environment"]
    assert "host.docker.internal" in env["OLLAMA_BASE_URL"]


def test_api_service_has_host_gateway_extra_host() -> None:
    extra_hosts = _api_service().get("extra_hosts", [])
    assert any("host.docker.internal:host-gateway" in str(h) for h in extra_hosts)


def test_webhook_secret_has_no_baked_default() -> None:
    env = _api_service()["environment"]
    secret = env["TELEGRAM_WEBHOOK_SECRET"]
    assert ":?" in secret, "TELEGRAM_WEBHOOK_SECRET must be required (:?), not defaulted"
    assert "local_webhook_secret" not in secret
