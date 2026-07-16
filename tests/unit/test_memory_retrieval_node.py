"""Tests for memory_retrieval_node (Phase 7d, T132-T140).

The node follows the LangGraph node contract (state, config) — the db session
travels in config["configurable"]["db"] (P0-1 regression: a (state, db)
signature received the RunnableConfig instead of the session, so recall was
always empty in the compiled graph).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent.nodes.memory_retrieval import memory_retrieval_node
from core.agent.state import AgentState, IntentEnum


@pytest.fixture
def mock_db():
    """Mock async database session."""
    return AsyncMock()


def make_config(db):
    """RunnableConfig-shaped dict as LangGraph passes it to nodes."""
    return {"configurable": {"db": db, "thread_id": "thread_001"}}


@pytest.fixture
def base_state():
    """Base AgentState for memory retrieval tests."""
    return AgentState(
        customer_id="cust_001",
        thread_id="thread_001",
        user_message="What's the price?",
        primary_intent=IntentEnum.PRICING,
        conversation_history=[],
    )


@pytest.mark.asyncio
async def test_memory_retrieval_missing_customer_id(mock_db):
    """T133: Missing customer_id → empty context, no exception.

    Ensures cold-start (new user) doesn't fail retrieval.
    """
    state = AgentState(
        customer_id=None,
        thread_id="thread_001",
        user_message="Hello",
        primary_intent=IntentEnum.INFO_QUERY,
        conversation_history=[],
    )

    result = await memory_retrieval_node(state, make_config(mock_db))

    assert result["memory_context"] == []
    assert result["memory_retrieval_scores"] == []
    assert not mock_db.called  # Should not query DB


@pytest.mark.asyncio
async def test_memory_retrieval_smalltalk_intent(base_state, mock_db):
    """T137: SMALLTALK routes directly to answer_node (no memory retrieval).

    Rationale: Small talk ("Hi", "Thanks") doesn't need context injection
    to avoid wasting retrieval queries.
    """
    base_state["primary_intent"] = IntentEnum.SMALLTALK

    result = await memory_retrieval_node(base_state, make_config(mock_db))

    assert result["memory_context"] == []
    assert result["memory_retrieval_scores"] == []


@pytest.mark.asyncio
async def test_memory_retrieval_smalltalk_via_router_intent_key(mock_db):
    """SMALLTALK skip also works with the "intent" key router_node actually sets."""
    state = AgentState(
        customer_id="cust_001",
        thread_id="thread_001",
        user_message="Hi",
        intent="SMALLTALK",
        conversation_history=[],
    )

    result = await memory_retrieval_node(state, make_config(mock_db))

    assert result["memory_context"] == []
    assert result["memory_retrieval_scores"] == []


@pytest.mark.asyncio
async def test_memory_retrieval_missing_db_in_config(base_state):
    """No db in config → empty context, no exception (graceful degradation)."""
    result = await memory_retrieval_node(base_state, {"configurable": {}})

    assert result["memory_context"] == []
    assert result["memory_retrieval_scores"] == []


@pytest.mark.asyncio
async def test_memory_retrieval_two_results(base_state, mock_db):
    """T134: 2 results → memory_context and scores have 2 entries.

    Tests that retrieval correctly maps results to state format.
    """
    # Mock SemanticMemoryService.retrieve()
    mock_result1 = MagicMock()
    mock_result1.summary_id = "summ_001"
    mock_result1.summary_text = "Customer bought a laptop in March"
    mock_result1.session_id = "thread_old_001"
    mock_result1.similarity_score = 0.92

    mock_result2 = MagicMock()
    mock_result2.summary_id = "summ_002"
    mock_result2.summary_text = "Asked about warranty coverage"
    mock_result2.session_id = "thread_old_002"
    mock_result2.similarity_score = 0.88

    # Patch the service at the import location
    mock_service_instance = AsyncMock()
    mock_service_instance.retrieve = AsyncMock(return_value=[mock_result1, mock_result2])

    with patch(
        "services.memory.semantic_memory.SemanticMemoryService",
        return_value=mock_service_instance,
    ):
        result = await memory_retrieval_node(base_state, make_config(mock_db))

        assert len(result["memory_context"]) == 2
        assert len(result["memory_retrieval_scores"]) == 2

        assert result["memory_context"][0]["summary_id"] == "summ_001"
        assert result["memory_context"][0]["summary_text"] == "Customer bought a laptop in March"
        assert result["memory_context"][0]["thread_id"] == "thread_old_001"

        assert result["memory_context"][1]["summary_id"] == "summ_002"
        assert result["memory_context"][1]["thread_id"] == "thread_old_002"

        assert result["memory_retrieval_scores"][0] == 0.92
        assert result["memory_retrieval_scores"][1] == 0.88

        # Verify retrieve() was called with correct parameters — including the
        # db session extracted from config["configurable"]["db"]
        mock_service_instance.retrieve.assert_called_once()
        call_args = mock_service_instance.retrieve.call_args
        assert call_args.kwargs["customer_id"] == "cust_001"
        assert call_args.kwargs["query"] == "What's the price?"
        assert call_args.kwargs["top_k"] == 3
        assert call_args.kwargs["min_score"] == 0.75
        assert call_args.kwargs["db"] is mock_db


@pytest.mark.asyncio
async def test_memory_retrieval_graceful_error_handling(base_state, mock_db):
    """T133 (edge): Exception during retrieval → empty context, no propagation.

    Ensures retrieval failures don't block the response.
    """
    mock_service_instance = AsyncMock()
    mock_service_instance.retrieve = AsyncMock(
        side_effect=RuntimeError("Database connection failed")
    )

    with patch(
        "services.memory.semantic_memory.SemanticMemoryService",
        return_value=mock_service_instance,
    ):
        result = await memory_retrieval_node(base_state, make_config(mock_db))

        # Should gracefully handle error and return empty context
        assert result["memory_context"] == []
        assert result["memory_retrieval_scores"] == []


@pytest.mark.asyncio
async def test_memory_retrieval_different_intents(mock_db):
    """T132 (edge): Test retrieval works for all non-SMALLTALK intents.

    Ensures memory is available for all customer interaction types.
    """
    intents_to_test = [
        IntentEnum.PRICING,
        IntentEnum.NEGOTIATION,
        IntentEnum.COMPLAINT,
        IntentEnum.FOLLOW_UP,
    ]

    mock_service_instance = AsyncMock()
    mock_service_instance.retrieve = AsyncMock(return_value=[])

    with patch(
        "services.memory.semantic_memory.SemanticMemoryService",
        return_value=mock_service_instance,
    ):
        for intent in intents_to_test:
            state = AgentState(
                customer_id="cust_001",
                thread_id="thread_001",
                user_message="Test message",
                primary_intent=intent,
                conversation_history=[],
            )

            result = await memory_retrieval_node(state, make_config(mock_db))

            # Should call retrieve for all non-SMALLTALK intents
            assert result["memory_context"] == []
            assert result["memory_retrieval_scores"] == []
            mock_service_instance.retrieve.assert_called()


@pytest.mark.asyncio
async def test_memory_retrieval_empty_results(base_state, mock_db):
    """T134 (edge): No results from retrieval → empty lists in state.

    Cold-start scenario where customer has no prior memory.
    """
    mock_service_instance = AsyncMock()
    mock_service_instance.retrieve = AsyncMock(return_value=[])

    with patch(
        "services.memory.semantic_memory.SemanticMemoryService",
        return_value=mock_service_instance,
    ):
        result = await memory_retrieval_node(base_state, make_config(mock_db))

        assert result["memory_context"] == []
        assert result["memory_retrieval_scores"] == []


@pytest.mark.asyncio
async def test_memory_retrieval_wired_through_langgraph():
    """P0-1 regression: LangGraph invokes nodes with (state, config).

    Registers the REAL memory_retrieval_node in a compiled StateGraph exactly
    like core/agent/graph.py does, invokes it through LangGraph, and asserts
    the db from config["configurable"]["db"] reaches the service. With the
    old (state, db) signature this test yields empty memory_context because
    the node received the RunnableConfig instead of the session.
    """
    from langgraph.graph import END, START, StateGraph

    mock_db = AsyncMock()

    mock_result = MagicMock()
    mock_result.summary_id = "summ_001"
    mock_result.summary_text = "Past conversation about laptops"
    mock_result.session_id = "thread_old_001"
    mock_result.similarity_score = 0.91

    mock_service_instance = AsyncMock()
    mock_service_instance.retrieve = AsyncMock(return_value=[mock_result])

    builder = StateGraph(AgentState)
    builder.add_node("memory_retrieval_node", memory_retrieval_node)
    builder.add_edge(START, "memory_retrieval_node")
    builder.add_edge("memory_retrieval_node", END)
    graph = builder.compile()

    with patch(
        "services.memory.semantic_memory.SemanticMemoryService",
        return_value=mock_service_instance,
    ):
        final_state = await graph.ainvoke(
            {
                "customer_id": "cust_001",
                "session_id": "thread_001",
                "user_message": "What's the price?",
                "intent": "PRICING",
                "messages": [],
            },
            config={"configurable": {"thread_id": "thread_001", "db": mock_db}},
        )

    assert final_state["memory_context"], "semantic recall must not be empty in the graph"
    assert final_state["memory_context"][0]["summary_id"] == "summ_001"
    assert final_state["memory_retrieval_scores"] == [0.91]
    assert mock_service_instance.retrieve.call_args.kwargs["db"] is mock_db


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
