"""WP4-001 Infra hardening tests: fail-fast secrets, JSON logging, PII masking.

Covers Feature 001 gaps (feature-scorecard.md):
  - Config: default secrets detected + ENV gate (FR: fail-fast in production)
  - Logging: JSON structured stdout + PII masking (FR-008)
"""

from __future__ import annotations

import json
import logging

import pytest

from core.config import Settings, find_insecure_default_secrets
from core.logging import (
    JsonFormatter,
    mask_email,
    mask_identifier,
    mask_phone,
    mask_pii,
)

# ---------------------------------------------------------------------------
# Config: ENV setting + insecure default detection
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> Settings:
    # _env_file=None: ignore .env so the test is deterministic across machines.
    return Settings(_env_file=None, **overrides)


def test_env_defaults_to_dev():
    assert _make_settings().ENV == "dev"


def test_powerful_model_typo_fixed():
    # Assert on the FIELD DEFAULT: litellm calls load_dotenv() on import, so a
    # model override in the developer's .env leaks into os.environ and would
    # make an instance-based assertion machine-dependent.
    default = Settings.model_fields["POWERFUL_CHAT_MODEL"].default
    assert "deepseek" in default
    assert "deepseel" not in default


def test_default_secrets_are_detected():
    # Explicit values == the shipped defaults; OS env may override real defaults.
    s = _make_settings(DB_PASSWORD="password", X_ADMIN_KEY="dev-secret-key")
    insecure = find_insecure_default_secrets(s)
    assert "DB_PASSWORD" in insecure
    assert "X_ADMIN_KEY" in insecure


def test_overridden_secrets_are_not_flagged():
    s = _make_settings(DB_PASSWORD="real-strong-password", X_ADMIN_KEY="real-admin-key")
    assert find_insecure_default_secrets(s) == []


@pytest.mark.asyncio
async def test_lifespan_fails_fast_in_production_with_default_secrets(monkeypatch):
    """App must refuse to start when ENV=production and secrets are defaults."""
    from api import main as api_main

    monkeypatch.setattr(api_main.settings, "ENV", "production")
    monkeypatch.setattr(api_main.settings, "DB_PASSWORD", "password")

    # Stub out infra so lifespan reaches the secret guard without a real DB.
    class _FakeConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *_args, **_kwargs):
            return None

    class _FakeEngine:
        sync_engine = None

        def connect(self):
            return _FakeConn()

    monkeypatch.setattr(api_main, "engine", _FakeEngine())
    monkeypatch.setattr(api_main, "instrument_sqlalchemy", lambda _engine: None)

    async def _fake_checkpointer(_url):
        return object()

    monkeypatch.setattr(api_main, "create_checkpointer", _fake_checkpointer)
    import core.agent.graph as graph_mod

    monkeypatch.setattr(graph_mod, "build_graph", lambda checkpointer=None: object())

    with pytest.raises(RuntimeError, match="insecure default secrets in production"):
        async with api_main.lifespan(api_main.app):
            pass


# ---------------------------------------------------------------------------
# PII masking helpers
# ---------------------------------------------------------------------------


def test_mask_email():
    assert mask_email("nguyen.van.a@example.com") == "ng***@example.com"
    assert mask_email("not-an-email") == "***"


def test_mask_phone():
    assert mask_phone("0912345678") == "*******678"
    assert mask_phone("+84 912 345 678") == "********678"
    assert mask_phone("12") == "***"


def test_mask_identifier():
    assert mask_identifier("tg:123456789") == "tg:***89"
    assert mask_identifier("ab") == "***"
    assert mask_identifier("user@example.com") == "us***@example.com"


def test_mask_pii_scrubs_free_text():
    text = "Khách nguyen.a@gmail.com gọi từ 0912345678 hỏi giá"
    masked = mask_pii(text)
    assert "nguyen.a@gmail.com" not in masked
    assert "0912345678" not in masked
    assert "ng***@gmail.com" in masked
    assert "678" in masked  # last 3 digits kept


def test_mask_pii_leaves_normal_text_alone():
    text = "Order 42 confirmed, latency 12.5ms"
    assert mask_pii(text) == text


# ---------------------------------------------------------------------------
# JSON structured formatter (FR-008)
# ---------------------------------------------------------------------------


def _format_record(msg: str, extra: dict | None = None) -> dict:
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return json.loads(JsonFormatter().format(record))


def test_json_formatter_core_fields():
    payload = _format_record("hello world")
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["message"] == "hello world"
    assert "timestamp" in payload


def test_json_formatter_masks_identity_extras():
    payload = _format_record("RTBF delete", extra={"customer_id": "tg:123456789"})
    assert payload["customer_id"] == "tg:***89"


def test_json_formatter_redacts_secret_like_extras():
    payload = _format_record("cfg", extra={"webhook_secret": "supersecretvalue"})
    assert payload["webhook_secret"] == "[REDACTED]"


def test_json_formatter_masks_pii_in_message():
    payload = _format_record("contact khách: 0912345678")
    assert "0912345678" not in payload["message"]


def test_json_formatter_includes_request_id_when_present():
    payload = _format_record("req", extra={"request_id": "req-abc-123"})
    assert payload["request_id"] == "req-abc-123"


def test_json_formatter_output_is_single_line_json():
    record_line = JsonFormatter().format(
        logging.LogRecord("x", logging.INFO, __file__, 1, "msg", (), None)
    )
    assert "\n" not in record_line
    json.loads(record_line)  # must parse
