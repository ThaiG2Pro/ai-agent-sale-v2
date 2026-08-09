"""Unit tests for router node (T057)."""

from unittest.mock import AsyncMock, patch

import pytest

from core.agent.nodes.router import _get_next_node, router_node
from core.agent.state import (
    IntentClassification,
    IntentEnum,
    make_initial_state,
)


@pytest.mark.asyncio
async def test_router_node_info_query():
    """Test router_node with INFO_QUERY intent (T057).

    Mock litellm.acompletion to return INFO_QUERY classification.
    Verify Command.goto == "retrieval_node" and state update.
    """
    # Create initial state
    initial_state = make_initial_state(
        "Giá sản phẩm X là bao nhiêu?", "test-session-001", "cust_001"
    )

    # Mock litellm.acompletion
    with patch("litellm.acompletion") as mock_completion:
        mock_message = AsyncMock()
        mock_message.content = IntentClassification(
            primary_intent=IntentEnum.INFO_QUERY,
            secondary_intents=[],
            confidence=0.9,
            reasoning="User asking about product price",
        ).model_dump_json()

        mock_choice = AsyncMock()
        mock_choice.message = mock_message

        mock_result = AsyncMock()
        mock_result.choices = [mock_choice]

        mock_completion.return_value = mock_result

        # Call router_node
        command = await router_node(initial_state)

        # Assertions
        assert command.goto == "retrieval_node"
        assert command.update["intent"] == "INFO_QUERY"
        assert command.update["intent_confidence"] == 0.9
        assert command.update["secondary_intents"] == []


@pytest.mark.asyncio
async def test_router_node_complaint():
    """Test router_node with COMPLAINT intent.

    Mock litellm to return COMPLAINT classification.
    Verify Command.goto == "escalation_node".
    """
    initial_state = make_initial_state("Tôi muốn khiếu nại", "test-session-002", "cust_001")

    with patch("litellm.acompletion") as mock_completion:
        mock_message = AsyncMock()
        mock_message.content = IntentClassification(
            primary_intent=IntentEnum.COMPLAINT,
            secondary_intents=[],
            confidence=0.85,
            reasoning="User expressing complaint",
        ).model_dump_json()

        mock_choice = AsyncMock()
        mock_choice.message = mock_message

        mock_result = AsyncMock()
        mock_result.choices = [mock_choice]

        mock_completion.return_value = mock_result

        command = await router_node(initial_state)

        assert command.goto == "escalation_node"
        assert command.update["intent"] == "COMPLAINT"


@pytest.mark.asyncio
async def test_router_node_smalltalk():
    """Test router_node with SMALLTALK intent.

    Mock litellm to return SMALLTALK.
    Verify Command.goto == "answer_node" (no retrieval).
    """
    initial_state = make_initial_state("Xin chào!", "test-session-003", "cust_001")

    with patch("litellm.acompletion") as mock_completion:
        mock_message = AsyncMock()
        mock_message.content = IntentClassification(
            primary_intent=IntentEnum.SMALLTALK,
            secondary_intents=[],
            confidence=0.95,
            reasoning="Greeting",
        ).model_dump_json()

        mock_choice = AsyncMock()
        mock_choice.message = mock_message

        mock_result = AsyncMock()
        mock_result.choices = [mock_choice]

        mock_completion.return_value = mock_result

        command = await router_node(initial_state)

        assert command.goto == "answer_node"
        assert command.update["intent"] == "SMALLTALK"


# ---------------------------------------------------------------------------
# WP3: router_node must never crash on malformed LLM output
# ---------------------------------------------------------------------------


def _make_llm_response(content: str):
    """Mock LiteLLM response with raw string content."""
    from unittest.mock import MagicMock

    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_content",
    [
        "this is not json at all {{{",  # garbage
        '{"primary_intent": "INFO_QUERY"}',  # missing required fields
        '{"primary_intent": "NOT_AN_INTENT", "confidence": 0.9, "reasoning": "x"}',  # bad enum
        "",  # empty
    ],
)
async def test_router_node_malformed_llm_output_falls_back(bad_content):
    """Malformed/incomplete JSON from LLM → fallback INFO_QUERY → retrieval_node."""
    initial_state = make_initial_state("Giá bao nhiêu?", "test-session-bad", "cust_001")

    with patch(
        "services.ai.ai_router.acompletion",
        new_callable=AsyncMock,
        return_value=_make_llm_response(bad_content),
    ):
        command = await router_node(initial_state)

    assert command.goto == "retrieval_node"
    assert command.update["intent"] == "INFO_QUERY"
    assert 0.0 <= command.update["intent_confidence"] <= 1.0
    assert command.update["secondary_intents"] == []


@pytest.mark.asyncio
async def test_router_node_llm_exception_falls_back():
    """LLM call raising entirely → fallback INFO_QUERY, no crash."""
    initial_state = make_initial_state("Giá bao nhiêu?", "test-session-err", "cust_001")

    with patch(
        "services.ai.AIGateway.complete",
        new_callable=AsyncMock,
        side_effect=RuntimeError("ollama down"),
    ):
        command = await router_node(initial_state)

    assert command.goto == "retrieval_node"
    assert command.update["intent"] == "INFO_QUERY"


def test_get_next_node_routing_map():
    """Test _get_next_node routing map (T045).

    Verify all intent → node routing is correct.
    """
    from core.agent.state import IntentClassification

    def make_clf(intent: IntentEnum) -> IntentClassification:
        return IntentClassification(
            primary_intent=intent, secondary_intents=[], confidence=0.9, reasoning="test"
        )

    # Escalation intents
    assert _get_next_node(make_clf(IntentEnum.COMPLAINT)) == "escalation_node"
    assert _get_next_node(make_clf(IntentEnum.NEGOTIATION)) == "escalation_node"

    # Answer node (no retrieval)
    assert _get_next_node(make_clf(IntentEnum.SMALLTALK)) == "answer_node"

    # Memory retrieval intents
    assert _get_next_node(make_clf(IntentEnum.FOLLOW_UP)) == "memory_retrieval_node"

    # Retrieval intents
    assert _get_next_node(make_clf(IntentEnum.INFO_QUERY)) == "retrieval_node"
    assert _get_next_node(make_clf(IntentEnum.PRICING)) == "retrieval_node"
    assert _get_next_node(make_clf(IntentEnum.COMPARISON)) == "retrieval_node"
    assert _get_next_node(make_clf(IntentEnum.AVAILABILITY)) == "retrieval_node"
