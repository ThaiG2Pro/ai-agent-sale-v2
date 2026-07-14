"""Regression test: process_telegram_message must run the REAL agent graph
end-to-end without mocking process_telegram_message itself.

Why this exists: the previous implementation built AgentState by hand with
`chat_id`/`update_id` keys instead of `make_initial_state()`, so router_node's
`state["user_message"]` raised KeyError on every real Telegram message. All
other Telegram tests monkeypatch process_telegram_message, so they never
exercised this code path and never caught it. This test invokes the real
function against a real compiled graph (LLM/retrieval mocked, DB real) to make
sure that regression can't reappear silently.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver

from core.agent.graph import build_graph
from core.telegram import message_handler
from core.telegram.models import TelegramUpdate
from tests.integration.test_agent_flow import _make_router_response, _mock_search_and_retrieve

pytestmark = pytest.mark.integration


def _unique_update_id() -> int:
    return int(time.time_ns() % 1_000_000_000)


@pytest.mark.asyncio
async def test_process_telegram_message_runs_real_graph_without_crashing(monkeypatch):
    """A real text message must flow through router -> retrieval -> confidence
    -> answer without KeyError, and must produce a real reply to the user."""
    chat_id = 555444333
    update = TelegramUpdate(
        update_id=_unique_update_id(),
        message={
            "message_id": 1,
            "from": {"id": chat_id, "is_bot": False, "first_name": "User"},
            "chat": {"id": chat_id, "type": "private"},
            "date": int(datetime.now(UTC).timestamp()),
            "text": "Điện thoại nào đang giảm giá?",
        },
    )

    # Layer-1 decline path: router runs once, retrieval reports low similarity,
    # confidence_node short-circuits to the canned decline message — no answer
    # LLM call needed, keeping the mock surface small.
    mock_llm = AsyncMock(side_effect=[_make_router_response("INFO_QUERY")])
    mock_retrieval = _mock_search_and_retrieve(similarity_score=0.40, declined=True)

    sent_messages: list[tuple[int, str]] = []

    async def fake_send(target_chat_id, text, reply_markup=None):
        sent_messages.append((target_chat_id, text))
        return True

    monkeypatch.setattr(message_handler, "send_telegram_message", fake_send)

    graph = build_graph(checkpointer=MemorySaver())

    with patch("services.ai.ai_router.acompletion", mock_llm):
        with patch("core.agent.nodes.retrieval.make_retrieval_tool", mock_retrieval):
            await message_handler.process_telegram_message(update, chat_id, graph=graph)

    assert len(sent_messages) == 1
    sent_chat_id, sent_text = sent_messages[0]
    assert sent_chat_id == chat_id
    assert sent_text  # non-empty real reply, not a crash fallback string
    assert "couldn't process" not in sent_text.lower()
