"""Unit tests for agent state and types."""

from core.agent.state import (
    AgentState,
    EscalationReasonEnum,
    IntentClassification,
    IntentEnum,
    make_initial_state,
)


def test_agent_state_imports():
    """Verify that all core agent state types are importable."""
    assert AgentState is not None
    assert IntentEnum is not None
    assert EscalationReasonEnum is not None


def test_intent_enum_values():
    """Verify IntentEnum has all 10 required values (Week 4 + Week 5)."""
    required = {
        "INFO_QUERY",
        "PRICING",
        "COMPARISON",
        "COMPLAINT",
        "NEGOTIATION",
        "SMALLTALK",
        "AVAILABILITY",
        "ORDER_PLACEMENT",
        "FOLLOW_UP",  # Week 5
        "OTHER",  # Week 5
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


def test_agent_state_fields_complete():
    """Verify AgentState TypedDict has all required fields from FR-001 (T055)."""
    required_fields = {
        "session_id",
        "user_message",
        "messages",
        "intent",
        "secondary_intents",
        "intent_confidence",
        "retrieved_chunks",
        "citations",
        "similarity_score",
        "rerank_score",
        "confidence_score",
        "model_used",
        "escalation_flag",
        "escalation_reason",
        "escalation_failure",
        "response",
        "declined",
        "error",
        "hitl_triggered",
        "hitl_reason",
        "hitl_pause_id",
        "hitl_rejection_reason",
        "hitl_escalation_count",
        "hitl_approved",
        "hitl_freshness_valid",
        "estimated_token_cost",
        "order_info",
        "cached_answer",
        "canonical_query",
        "query_vector",
        "pending_info_questions",
        # Week 5 fields
        "customer_id",
        "memory_context",
        "memory_retrieval_scores",
        "thread_summary_exists",
        "sales_intent_skipped",
        # WP-V2-3 clarify loop fields
        "needs_clarification",
        "awaiting_clarification",
        "clarify_original_query",
        "clarify_count",
    }
    actual_fields = set(AgentState.__annotations__.keys())
    assert required_fields == actual_fields, (
        f"Missing or extra fields: {required_fields ^ actual_fields}"
    )


def test_intent_enum_serialization():
    """Verify IntentEnum values serialize correctly to string (T056)."""
    # StrEnum serializes directly to value (not full repr)
    assert str(IntentEnum.INFO_QUERY) == "INFO_QUERY"
    assert IntentEnum.INFO_QUERY.value == "INFO_QUERY"

    # All enum values must be valid
    for intent in IntentEnum:
        assert isinstance(intent.value, str)
        assert len(intent.value) > 0


def test_make_initial_state():
    """Verify make_initial_state() factory creates valid initial state (T054)."""
    state = make_initial_state("Test query", "test-session-123", customer_id="test-cust-1")

    # Check type
    assert isinstance(state, dict)

    # Check all required fields exist
    assert state["session_id"] == "test-session-123"
    assert state["user_message"] == "Test query"
    assert state["messages"] == []

    # Check bool flags are explicitly False (not None)
    assert state["escalation_flag"] is False
    assert state["escalation_failure"] is False
    assert state["declined"] is False
    assert isinstance(state["escalation_flag"], bool)
    assert isinstance(state["escalation_failure"], bool)
    assert isinstance(state["declined"], bool)

    # Check numeric defaults
    assert state["intent_confidence"] == 0.0
    assert state["similarity_score"] == 0.0
    assert state["confidence_score"] == 0.0

    # Check optional fields
    assert state["intent"] is None
    assert state["rerank_score"] is None
    assert state["model_used"] is None
    assert state["response"] is None


def test_make_initial_state_missing_customer_id():
    """T019: Raises TypeError when customer_id is missing (required positional arg)."""
    import pytest

    with pytest.raises(TypeError):
        # Missing required customer_id positional argument
        make_initial_state(user_message="Hello", session_id="session_1")  # type: ignore


def test_make_initial_state_empty_customer_id():
    """T019: Raises ValueError when customer_id is empty string."""
    import pytest

    with pytest.raises(ValueError, match="customer_id cannot be empty"):
        make_initial_state(
            user_message="Hello",
            session_id="session_1",
            customer_id="",
        )


def test_week5_fields_initialized():
    """T020: All 5 new Week 5 fields initialized with correct defaults."""
    state = make_initial_state(
        user_message="Hello",
        session_id="telegram:12345",
        customer_id="12345",
    )

    # Verify 5 new Week 5 fields exist and have correct defaults
    assert state["customer_id"] == "12345", "customer_id not set correctly"
    assert state["memory_context"] == [], "memory_context not initialized to empty list"
    assert state["memory_retrieval_scores"] == [], "memory_retrieval_scores not initialized"
    assert state["thread_summary_exists"] is False, "thread_summary_exists not False"
    assert state["sales_intent_skipped"] is False, "sales_intent_skipped not False"
