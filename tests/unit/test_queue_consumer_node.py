"""Unit tests for queue_consumer_node (T060, T061)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from core.agent.nodes.queue_consumer import queue_consumer_node
from services.hitl.schemas import QueuedMessageBatch, QueueIntentResult


@pytest.fixture
def mock_db():
    mock = AsyncMock()
    # Mock return value for scalars().all()
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = []
    mock.execute.return_value = mock_result
    return mock


@pytest.fixture
def mock_config(mock_db):
    return {"configurable": {"db": mock_db}}


@pytest.fixture
def initial_state():
    return {
        "session_id": "test-session",
        "messages": [],
        "hitl_approved": True,
        "hitl_escalation_count": 0,
    }


@pytest.mark.asyncio
async def test_queue_consumer_orphan_tool_scan(initial_state, mock_config):
    """Test that orphan tool calls are closed (T060)."""
    initial_state["messages"] = [
        AIMessage(
            content="using tool", tool_calls=[{"id": "call_1", "name": "get_product", "args": {}}]
        )
    ]

    result = await queue_consumer_node(initial_state, mock_config)

    messages = result.update["messages"]
    assert len(messages) == 2
    assert isinstance(messages[1], ToolMessage)
    assert messages[1].tool_call_id == "call_1"
    assert "cancelled" in messages[1].content


@pytest.mark.asyncio
async def test_queue_consumer_empty_queue(initial_state, mock_config, mock_db):
    """Test path with no queued messages (T060)."""
    result = await queue_consumer_node(initial_state, mock_config)

    assert result.goto == "state_freshness_validator_node"
    assert "messages" not in result.update  # No humans messages added


@pytest.mark.asyncio
async def test_queue_consumer_cancel_override(initial_state, mock_config, mock_db):
    """Test that CANCEL in queue overrides admin approval (T061)."""
    # 1. Mock DB to return one message
    mock_msg = MagicMock()
    mock_msg.message_id = "msg_1"
    mock_msg.message_text = "Stop everything"

    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [mock_msg]
    mock_db.execute.return_value = mock_result

    # 2. Mock LiteLLM to return CANCEL intent
    batch_result = QueuedMessageBatch(
        session_id="test-session",
        messages=[
            QueueIntentResult(message_id="msg_1", text="Stop", intent="CANCEL", confidence=0.9)
        ],
    )

    mock_response = MagicMock()
    mock_response.choices[0].message.content = batch_result

    with patch("litellm.acompletion", return_value=mock_response):
        result = await queue_consumer_node(initial_state, mock_config)

        assert result.goto == "cancellation_node"
        assert len(result.update["messages"]) == 1  # The enqueued human message


@pytest.mark.asyncio
async def test_queue_consumer_modify_re_pause(initial_state, mock_config, mock_db):
    """Test that MODIFY intent causes re-pause (T061)."""
    # 1. Mock DB
    mock_msg = MagicMock()
    mock_msg.message_id = "msg_1"
    mock_msg.message_text = "I want 2 instead of 1"

    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [mock_msg]
    mock_db.execute.return_value = mock_result

    # 2. Mock LiteLLM to return MODIFY_ORDER
    batch_result = QueuedMessageBatch(
        session_id="test-session",
        messages=[
            QueueIntentResult(message_id="msg_1", text="2", intent="MODIFY_ORDER", confidence=0.9)
        ],
    )

    mock_response = MagicMock()
    mock_response.choices[0].message.content = batch_result

    with patch("litellm.acompletion", return_value=mock_response):
        result = await queue_consumer_node(initial_state, mock_config)

        assert result.goto == "hitl_guard_node"
        assert result.update["hitl_escalation_count"] == 1
        assert result.update["hitl_triggered"] is False  # Reset for hitl_guard to re-trigger


# ---------------------------------------------------------------------------
# Keyword heuristic tests (model-size-agnostic — no LLM call needed)
# ---------------------------------------------------------------------------
from core.agent.nodes.queue_consumer import _keyword_classify_batch  # noqa: E402


def _make_row(text: str, msg_id: str = "msg-1"):
    row = MagicMock()
    row.message_id = msg_id
    row.message_text = text
    return row


@pytest.mark.parametrize(
    "text",
    [
        "tôi đổi ý rồi. lấy Xiaomi 14 ultra đi",
        "thay sang Samsung Galaxy S24",
        "đặt iPhone thay cho cái kia",
        "lấy Lenovo thay đi nhé",
        "I changed my mind, get the Dell instead",
        "đổi qua MacBook Pro đi",
        "muốn đổi sản phẩm khác",
    ],
)
def test_keyword_heuristic_modify_order(text):
    """Keyword heuristic must catch Vietnamese change-of-mind phrases as MODIFY_ORDER."""
    result = _keyword_classify_batch("s1", [_make_row(text)])
    assert result is not None, f"Should not fall back to LLM for: {text!r}"
    assert result.has_modify is True
    assert result.has_cancel is False


@pytest.mark.parametrize(
    "text",
    [
        "huỷ đơn hàng",
        "không mua nữa",
        "thôi không đặt nữa",
        "cancel the order",
        "bỏ đơn đi",
    ],
)
def test_keyword_heuristic_cancel(text):
    """Keyword heuristic must catch Vietnamese cancel phrases."""
    result = _keyword_classify_batch("s1", [_make_row(text)])
    assert result is not None
    assert result.has_cancel is True
    assert result.has_modify is False


@pytest.mark.parametrize(
    "text",
    [
        "ok",
        "đồng ý",
        "được rồi",
        "yes",
        "xác nhận",
    ],
)
def test_keyword_heuristic_confirm(text):
    """Single-word confirmations should be caught by heuristic."""
    result = _keyword_classify_batch("s1", [_make_row(text)])
    assert result is not None
    assert result.has_confirm is True


def test_keyword_heuristic_ambiguous_returns_none():
    """Ambiguous messages must return None → fall through to LLM."""
    result = _keyword_classify_batch("s1", [_make_row("tôi muốn hỏi thêm về sản phẩm")])
    assert result is None  # LLM should handle this


def test_keyword_heuristic_cancel_takes_priority_over_modify():
    """When batch has both CANCEL and MODIFY, has_cancel wins (highest priority)."""
    rows = [
        _make_row("huỷ đơn đi", "m1"),
        _make_row("thay sang Xiaomi đi", "m2"),
    ]
    result = _keyword_classify_batch("s1", rows)
    assert result is not None
    assert result.has_cancel is True
    assert result.has_modify is True  # Both flagged; caller checks cancel first


@pytest.mark.asyncio
async def test_sc5_vietnamese_change_of_mind_no_llm_call(initial_state, mock_config, mock_db):
    """SC5 regression: 'đổi ý rồi. lấy Xiaomi 14 ultra đi' must route to hitl_guard_node
    WITHOUT calling the LLM — keyword heuristic catches it deterministically.

    _resolve_new_product_from_modify is stubbed out: it runs the real retrieval
    pipeline (retrieve_with_retry → rewrite_query, its own LLM), which is not
    under test here — it has its own suite. Historically this test only passed
    because the embedding step crashed against an offline Ollama; with the
    local/ embed path retrieval works in unit tests, so the boundary must be
    mocked explicitly."""
    mock_msg = _make_row("tôi đổi ý rồi. lấy Xiaomi 14 ultra đi")
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [mock_msg]
    mock_db.execute.return_value = mock_result

    with (
        patch("litellm.acompletion") as mock_llm,
        patch(
            "core.agent.nodes.queue_consumer._resolve_new_product_from_modify",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await queue_consumer_node(initial_state, mock_config)

        # Must route to re-pause
        assert result.goto == "hitl_guard_node", "Change-of-mind must re-pause"
        assert result.update["hitl_escalation_count"] == 1
        # hitl_approved must be reset so hitl_guard_node re-evaluates (not skip to answer_node)
        assert result.update["hitl_approved"] is False
        # LLM must NOT have been called (keyword heuristic handles it)
        mock_llm.assert_not_called()
