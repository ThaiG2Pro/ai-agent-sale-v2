"""support_router_node — four intents, two destinations, never an order flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from core.agent.state import IntentEnum, make_initial_state
from core.support.nodes.router import (
    SUPPORT_INTENTS,
    normalize_support_intent,
    support_router_node,
)

_PATCH = "core.support.nodes.router.AIGateway.complete_json"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("INFO_QUERY", IntentEnum.INFO_QUERY),
        ("pricing", IntentEnum.PRICING),
        ("COMPLAINT", IntentEnum.COMPLAINT),
        ("SMALLTALK", IntentEnum.SMALLTALK),
        ("GREETING", IntentEnum.SMALLTALK),
        ("REFUND", IntentEnum.COMPLAINT),
        ("ORDER_PLACEMENT", IntentEnum.INFO_QUERY),
        ("NEGOTIATION", IntentEnum.INFO_QUERY),
        ("CANCEL", IntentEnum.INFO_QUERY),
        ("FOLLOW_UP", IntentEnum.INFO_QUERY),
        ("", IntentEnum.INFO_QUERY),
        (None, IntentEnum.INFO_QUERY),
        ("garbage", IntentEnum.INFO_QUERY),
    ],
)
def test_normalize_support_intent(raw, expected):
    out = normalize_support_intent(raw)
    assert out == expected
    assert out in SUPPORT_INTENTS


@pytest.mark.asyncio
async def test_smalltalk_fastpath_skips_llm():
    state = make_initial_state("xin chào", "s1", "c1")
    with patch(_PATCH, new=AsyncMock()) as mock_json:
        cmd = await support_router_node(state)
    mock_json.assert_not_awaited()
    assert cmd.goto == "support_answer_node"
    assert cmd.update["intent"] == "SMALLTALK"
    assert cmd.update["smalltalk_fastpath"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("llm_intent", "goto", "stored"),
    [
        ("INFO_QUERY", "retrieval_node", "INFO_QUERY"),
        ("PRICING", "retrieval_node", "PRICING"),
        ("COMPLAINT", "retrieval_node", "COMPLAINT"),
        ("SMALLTALK", "support_answer_node", "SMALLTALK"),
        # shop labels must never reach order/cancel/HITL nodes
        ("ORDER_PLACEMENT", "retrieval_node", "INFO_QUERY"),
        ("CANCEL", "retrieval_node", "INFO_QUERY"),
    ],
)
async def test_llm_classification_routes(llm_intent, goto, stored):
    state = make_initial_state("Clone space của người khác được không?", "s1", "c1")
    payload = {"primary_intent": llm_intent, "confidence": 0.87, "reasoning": "t"}
    with patch(_PATCH, new=AsyncMock(return_value=payload)):
        cmd = await support_router_node(state)
    assert cmd.goto == goto
    assert cmd.update["intent"] == stored
    assert cmd.update["intent_confidence"] == pytest.approx(0.87)
    assert cmd.update["secondary_intents"] == []
    assert cmd.update["smalltalk_fastpath"] is False


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_info_query():
    state = make_initial_state("tạo quiz bị lỗi", "s1", "c1")
    with patch(_PATCH, new=AsyncMock(side_effect=RuntimeError("boom"))):
        cmd = await support_router_node(state)
    assert cmd.goto == "retrieval_node"
    assert cmd.update["intent"] == "INFO_QUERY"
    assert cmd.update["intent_confidence"] == 0.0


@pytest.mark.asyncio
async def test_history_is_passed_as_context_and_only_last_message_classified():
    state = make_initial_state("vậy mua thế nào?", "s1", "c1")
    state["intent"] = "INFO_QUERY"
    state["messages"] = [
        HumanMessage(content="credit là gì?"),
        AIMessage(content="Credit là đơn vị để AI tạo quiz…"),
        HumanMessage(content="vậy mua thế nào?"),
    ]
    payload = {"primary_intent": "PRICING", "confidence": 0.8, "reasoning": "t"}
    with patch(_PATCH, new=AsyncMock(return_value=payload)) as mock_json:
        cmd = await support_router_node(state)
    sent = mock_json.await_args.kwargs["messages"]
    assert "Classify ONLY the LAST" in sent[0]["content"]
    assert "Customer: credit là gì?" in sent[1]["content"]
    assert sent[1]["content"].rstrip().endswith("vậy mua thế nào?")
    assert mock_json.await_args.kwargs["model"] == "light-chat"
    assert cmd.update["intent"] == "PRICING"
    assert cmd.update["intent_shift"] is True
