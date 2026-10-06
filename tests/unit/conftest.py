"""Unit-test defaults: no live LLM calls from code paths a test did not mock.

ORDER_SLOT_LLM_ENABLED is on in production; unit tests start with the regex-only
path and enable the LLM path explicitly (tests/unit/test_node_hardening.py).
"""

import pytest

from core.config import settings


@pytest.fixture(autouse=True)
def _no_live_slot_llm(monkeypatch):
    monkeypatch.setattr(settings, "ORDER_SLOT_LLM_ENABLED", False)
    monkeypatch.setattr(settings, "INPUT_GUARD_ENABLED", False)
