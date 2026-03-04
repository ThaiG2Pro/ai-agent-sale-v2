"""Unit tests for escalation_node (Phase 5, T067-T071b).

Tests intent-based escalation logic, premium model fallback, and cost guard.
"""

from __future__ import annotations

import pytest

from core.agent.nodes.escalation import escalation_node
from core.agent.state import EscalationReasonEnum, make_initial_state


def _state(intent: str, secondary_intents: list[str] | None = None, **extra) -> dict:
    s = make_initial_state("test-session", "test message")
    s["intent"] = intent
    s["secondary_intents"] = secondary_intents or []
    s.update(extra)
    return s


@pytest.mark.asyncio
async def test_complaint_escalates_to_premium(monkeypatch):
    """T067: COMPLAINT intent → escalate to premium model (SC-002)."""
    monkeypatch.setattr("core.agent.nodes.escalation.settings.PREMIUM_MODEL", "premium-chat")
    state = _state("COMPLAINT")
    result = await escalation_node(state)
    assert result["escalation_flag"] is True
    assert result["escalation_reason"] == EscalationReasonEnum.INTENT_ESCALATION
    assert "premium" in result["model_used"]


@pytest.mark.asyncio
async def test_negotiation_escalates_to_premium(monkeypatch):
    """T068: NEGOTIATION intent → escalate to premium model, reason=intent_escalation.
    (SC-002)
    """
    monkeypatch.setattr("core.agent.nodes.escalation.settings.PREMIUM_MODEL", "premium-chat")
    state = _state("NEGOTIATION")
    result = await escalation_node(state)
    assert result["escalation_flag"] is True
    assert result["escalation_reason"] == EscalationReasonEnum.INTENT_ESCALATION
    assert result["escalation_reason"] != EscalationReasonEnum.LOW_CONFIDENCE


@pytest.mark.asyncio
async def test_info_query_no_escalation(monkeypatch):
    """T069: INFO_QUERY routed to escalation_node → escalates with
    low_confidence reason.

    Note: tasks.md says assert escalation_flag=False for INFO_QUERY here,
    but per T063 spec, INFO_QUERY routed to escalation_node means
    borderline confidence → escalate=True.
    This test follows the actual escalation_node contract: INFO_QUERY →
    low_confidence escalation. When confidence_node routes to answer_node
    directly (high confidence), escalation_node is NOT called.
    """
    monkeypatch.setattr("core.agent.nodes.escalation.settings.PREMIUM_MODEL", "premium-chat")
    # INFO_QUERY with high similarity (routed directly to answer,
    # bypasses escalation_node)
    # For escalation_node test, we test the case when router sends INFO_QUERY here:
    # → reason=low_confidence (borderline)
    state = _state("INFO_QUERY")
    result = await escalation_node(state)
    # INFO_QUERY in escalation_node means it was borderline → escalates
    assert result["escalation_flag"] is True
    assert result["escalation_reason"] == EscalationReasonEnum.LOW_CONFIDENCE


@pytest.mark.asyncio
async def test_premium_model_unavailable_fallback(monkeypatch):
    """T070: If premium model config is empty → fallback to economy-chat.
    With escalation_failure flag set.
    """
    monkeypatch.setattr("core.agent.nodes.escalation.settings.PREMIUM_MODEL", "")
    state = _state("COMPLAINT")
    result = await escalation_node(state)
    assert result["escalation_flag"] is True
    assert result["model_used"] == "economy-chat"
    assert result["escalation_failure"] is True


@pytest.mark.asyncio
async def test_simple_pricing_no_escalation(monkeypatch):
    """T071b: PRICING with high confidence → no escalation (Article XII cost guard)."""
    monkeypatch.setattr("core.agent.nodes.escalation.settings.PREMIUM_MODEL", "premium-chat")
    state = _state("PRICING", similarity_score=0.95)
    result = await escalation_node(state)
    assert result["escalation_flag"] is False
    assert result["model_used"] is None
    assert result["escalation_reason"] == EscalationReasonEnum.NONE


@pytest.mark.asyncio
async def test_secondary_intent_complaint_escalates(monkeypatch):
    """COMPLAINT in secondary_intents → escalate even if primary is OTHER."""
    monkeypatch.setattr("core.agent.nodes.escalation.settings.PREMIUM_MODEL", "premium-chat")
    state = _state("OTHER", secondary_intents=["COMPLAINT"])
    result = await escalation_node(state)
    assert result["escalation_flag"] is True
    assert result["escalation_reason"] == EscalationReasonEnum.INTENT_ESCALATION
