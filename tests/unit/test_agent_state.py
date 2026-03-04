"""Unit tests for agent state and types."""

from core.agent.state import (
    AgentState,
    EscalationReasonEnum,
    IntentClassification,
    IntentEnum,
)


def test_agent_state_imports():
    """Verify that all core agent state types are importable."""
    assert AgentState is not None
    assert IntentEnum is not None
    assert EscalationReasonEnum is not None


def test_intent_enum_values():
    """Verify IntentEnum has all 7 required values."""
    required = {
        "INFO_QUERY",
        "PRICING",
        "COMPARISON",
        "COMPLAINT",
        "NEGOTIATION",
        "SMALLTALK",
        "AVAILABILITY",
    }
    actual = {e.value for e in IntentEnum}
    assert required == actual


def test_escalation_reason_enum_values():
    """Verify EscalationReasonEnum has 3 required values."""
    required = {"intent_escalation", "low_confidence", "none"}
    actual = {e.value for e in EscalationReasonEnum}
    assert required == actual


def test_intent_classification_has_escalation_intent():
    """Verify IntentClassification.has_escalation_intent() logic."""
    # Primary intent = COMPLAINT
    ic1 = IntentClassification(
        primary_intent=IntentEnum.COMPLAINT,
        secondary_intents=[],
        confidence=0.9,
        reasoning="User expressed complaint",
    )
    assert ic1.has_escalation_intent() is True

    # Primary intent = INFO_QUERY, secondary = NEGOTIATION
    ic2 = IntentClassification(
        primary_intent=IntentEnum.INFO_QUERY,
        secondary_intents=[IntentEnum.NEGOTIATION],
        confidence=0.8,
        reasoning="Query with negotiation signal",
    )
    assert ic2.has_escalation_intent() is True

    # Primary intent = INFO_QUERY, no escalation signals
    ic3 = IntentClassification(
        primary_intent=IntentEnum.INFO_QUERY,
        secondary_intents=[],
        confidence=0.95,
        reasoning="Simple info query",
    )
    assert ic3.has_escalation_intent() is False
