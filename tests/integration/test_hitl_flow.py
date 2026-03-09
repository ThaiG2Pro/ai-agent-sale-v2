"""Integration tests for HITL flow (T065, T066, T067).

Tests end-to-end execution with interrupts, resumes, and queue processing.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from core.agent.graph import build_graph
from core.agent.state import make_initial_state
from services.hitl.schemas import ApprovalPayload, QueuedMessageBatch, QueueIntentResult

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Mock Helpers
# ---------------------------------------------------------------------------


def _make_router_response(intent: str, confidence: float = 0.9):
    """Make mock LiteLLM response for router_node."""
    content = json.dumps(
        {
            "primary_intent": intent,
            "secondary_intents": [],
            "confidence": confidence,
            "reasoning": "test reasoning",
        },
    )
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_answer_response(text: str = "Test answer"):
    """Make mock LiteLLM response for nodes."""
    choice = MagicMock()
    choice.message.content = text
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_retrieval_result(similarity_score: float, declined: bool):
    """Build a mock RetrievalResult for patching search_and_retrieve."""
    from services.rag.pipeline import RetrievalResult

    return RetrievalResult(
        cached_answer=None,
        cached_citations=[],
        declined=declined,
        citations=[],
        chunks=[],
        best_similarity=similarity_score,
        similarity_gap=0.0,
        canonical_query="test query",
        query_vector=[0.1] * 10,
        query_category="INFO_QUERY",
        top_k_used=5,
    )


def _mock_retrieval_tool(similarity_score: float, declined: bool):
    """Mock for make_retrieval_tool."""
    result = _make_retrieval_result(similarity_score, declined)
    mock_tool = MagicMock()
    mock_tool.ainvoke = AsyncMock(return_value=result)

    def mock_factory(db):
        return mock_tool

    return mock_factory


# ---------------------------------------------------------------------------
# Phase 19: Integration Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hitl_happy_path_approve_execute(monkeypatch):
    """T065: ORDER_PLACEMENT triggers pause → admin approve → order executed."""
    # 1. Setup mocks
    mock_db = AsyncMock()
    # Configure execute() result so scalars().all() returns [] (no queued messages)
    # and scalar_one_or_none() returns the mock product (for state_freshness_validator).
    mock_exec_result = MagicMock(rowcount=1)
    mock_exec_result.scalars.return_value.all.return_value = []
    mock_product = MagicMock()
    mock_product.id = "prod_1"
    mock_product.price = 100.0
    mock_product.stock_quantity = 10
    mock_exec_result.scalar_one_or_none.return_value = mock_product
    mock_db.execute.return_value = mock_exec_result

    # Mock router
    mock_router_resp = _make_router_response("ORDER_PLACEMENT")

    # Mock retrieval
    mock_retrieval = _mock_retrieval_tool(similarity_score=0.9, declined=False)

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-happy", "db": mock_db}}
    state = make_initial_state("Tôi muốn mua sản phẩm A", "test-happy")
    state["order_info"] = {"product_id": "prod_1", "quantity": 1, "price": 100.0}

    with patch("services.ai.ai_router.acompletion", return_value=mock_router_resp):
        with patch("core.agent.nodes.retrieval.make_retrieval_tool", mock_retrieval):
            with patch("litellm.token_counter", return_value=100):
                # 2. Run graph until pause
                await graph.ainvoke(state, config)

                snapshot = await graph.aget_state(config)
                assert snapshot.next == ("hitl_guard_node",)

                # 3. Simulate admin approval
                payload = ApprovalPayload(action="approve", admin_user_id="admin_1")

                # Resume (queue_consumer will see empty queue and NOT call litellm)
                # answer_node will see response already set and NOT call litellm
                resume_cmd = Command(resume=payload.model_dump())
                async for _event in graph.astream(resume_cmd, config):
                    print(f"EVENT: {_event}")

                final_result = await graph.aget_state(config)
                final_state = final_result.values

                # 4. Assert
                assert final_state["hitl_approved"] is True
                assert final_state["order_info"]["status"] == "confirmed"
                assert "successfully placed" in final_state["response"]


@pytest.mark.asyncio
async def test_hitl_reject_to_support(monkeypatch):
    """T066: ORDER_PLACEMENT → pause → admin reject → customer_support."""
    mock_db = AsyncMock()
    mock_db.execute.return_value = MagicMock()

    # Mock router
    mock_router_resp = _make_router_response("ORDER_PLACEMENT")

    # Mock litellm for customer_support
    mock_support_resp = _make_answer_response("Rejected by admin")

    mock_retrieval = _mock_retrieval_tool(similarity_score=0.9, declined=False)

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-reject", "db": mock_db}}
    state = make_initial_state("Mua đồ", "test-reject")
    state["order_info"] = {"product_id": "prod_1", "quantity": 1}

    with patch("services.ai.ai_router.acompletion", return_value=mock_router_resp):
        with patch("litellm.acompletion", return_value=mock_support_resp):
            with patch("core.agent.nodes.retrieval.make_retrieval_tool", mock_retrieval):
                with patch("litellm.token_counter", return_value=100):
                    # 1. Pause
                    await graph.ainvoke(state, config)

                    # 2. Reject
                    payload = ApprovalPayload(
                        action="reject", admin_user_id="admin_1", reason_or_comment="suspicious"
                    )

                    final_result = await graph.ainvoke(
                        Command(resume=payload.model_dump()), config
                    )

                    # 3. Assert
                    assert final_result["hitl_approved"] is False
                    assert final_result["hitl_rejection_reason"] == "suspicious"
                    assert "Rejected" in final_result["response"]


@pytest.mark.asyncio
async def test_hitl_cancel_during_pause(monkeypatch):
    """T067: Pause → Customer sends CANCEL → Admin approves → Graph routes to cancel."""
    mock_db = AsyncMock()

    # Mock product for freshness
    mock_product = MagicMock()
    mock_product.id = "prod_1"
    mock_product.price = 100.0
    mock_product.stock_quantity = 10

    # Queued message found in queue_consumer_node
    mock_queued_msg = MagicMock()
    mock_queued_msg.message_id = "msg_1"
    mock_queued_msg.message_text = "Hủy đơn hàng này đi"

    mock_execute_result = MagicMock()
    mock_execute_result.scalars().all.return_value = [mock_queued_msg]
    mock_execute_result.scalar_one_or_none.return_value = mock_product
    mock_db.execute.return_value = mock_execute_result

    # Mock router
    mock_router_resp = _make_router_response("ORDER_PLACEMENT")

    # Batch result with has_cancel=True
    msg = QueueIntentResult(message_id="msg_1", text="cancel", intent="CANCEL", confidence=0.9)
    batch_result = QueuedMessageBatch(session_id="test-cancel", messages=[msg])

    # Mock classification response
    mock_classify_resp = MagicMock()
    mock_classify_resp.choices[0].message.content = batch_result

    mock_retrieval = _mock_retrieval_tool(similarity_score=0.9, declined=False)

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-cancel", "db": mock_db}}
    state = make_initial_state("Mua cái này", "test-cancel")
    state["order_info"] = {"product_id": "prod_1", "quantity": 1, "price": 100.0}

    with patch("services.ai.ai_router.acompletion", return_value=mock_router_resp):
        with patch("litellm.acompletion", return_value=mock_classify_resp):
            with patch("core.agent.nodes.retrieval.make_retrieval_tool", mock_retrieval):
                with patch("litellm.token_counter", return_value=100):
                    # 1. Pause
                    await graph.ainvoke(state, config)

                    # 2. Admin approves, but customer enqueued CANCEL
                    payload = ApprovalPayload(action="approve", admin_user_id="admin_1")

                    resume_cmd = Command(resume=payload.model_dump())
                    async for _event in graph.astream(resume_cmd, config):
                        pass

                    final_result = await graph.aget_state(config)
                    final_state = final_result.values

                    # 3. Assert
                    assert final_state["order_info"]["status"] == "cancelled"
                    assert "cancelled" in final_state["response"].lower()
