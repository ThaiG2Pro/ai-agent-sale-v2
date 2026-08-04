"""Unit tests for core/telegram/message_handler.py (T037-T042).

Covers the dispatch paths: non-text message, /inventory command (success +
retryable failure), paused-HITL-session queueing, normal agent turn with
post-turn memory dispatch, HITL pause acknowledgement, and the error shield.
All collaborators (graph, DB, Telegram send) are mocked.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.telegram.message_handler import _HITL_PENDING_MESSAGE, process_telegram_message

CHAT_ID = 42


def _update(text: str | None) -> MagicMock:
    upd = MagicMock()
    upd.update_id = 1
    upd.get_text.return_value = text
    return upd


def _graph(response: str | None = "Dạ có ạ!", paused: bool = False) -> AsyncMock:
    graph = AsyncMock()
    graph.ainvoke.return_value = {"response": response}
    graph.aget_state.return_value = MagicMock(next=("hitl_guard_node",) if paused else ())
    return graph


def _session_local() -> MagicMock:
    ctx = AsyncMock()
    ctx.__aenter__.return_value = AsyncMock()
    ctx.__aexit__.return_value = False
    return MagicMock(return_value=ctx)


@pytest.fixture
def send_mock():
    with patch(
        "core.telegram.message_handler.send_telegram_message",
        AsyncMock(return_value=True),
    ) as m:
        yield m


@pytest.mark.asyncio
async def test_non_text_message_gets_polite_reply(send_mock):
    await process_telegram_message(_update(None), CHAT_ID)
    send_mock.assert_awaited_once()
    assert "text messages" in send_mock.await_args.args[1]


@pytest.mark.asyncio
async def test_inventory_command_success(send_mock):
    tool_result = SimpleNamespace(
        success=True, data=SimpleNamespace(sku="PROD-001", stock_level=7)
    )
    with patch(
        "core.telegram.message_handler.execute_inventory_lookup",
        AsyncMock(return_value=tool_result),
    ):
        await process_telegram_message(_update("/inventory prod-001"), CHAT_ID)

    msg = send_mock.await_args.args[1]
    assert "PROD-001" in msg and "7 units" in msg


@pytest.mark.asyncio
async def test_inventory_command_retryable_failure_offers_retry(send_mock):
    tool_result = SimpleNamespace(success=False, error="timeout", is_retryable=True)
    with (
        patch(
            "core.telegram.message_handler.execute_inventory_lookup",
            AsyncMock(return_value=tool_result),
        ),
        patch(
            "core.telegram.message_handler.create_retry_keyboard",
            MagicMock(return_value={"kb": True}),
        ) as kb,
    ):
        await process_telegram_message(_update("/inventory X"), CHAT_ID)

    kb.assert_called_once_with("inventory_check", "X")
    assert send_mock.await_args.kwargs.get("reply_markup") == {"kb": True} or (
        len(send_mock.await_args.args) > 2 and send_mock.await_args.args[2] == {"kb": True}
    )


@pytest.mark.asyncio
async def test_paused_session_queues_instead_of_reinvoking(send_mock):
    graph = _graph()
    with (
        patch("core.telegram.message_handler.AsyncSessionLocal", _session_local()),
        patch(
            "api.dependencies.check_paused_session",
            AsyncMock(return_value={"queued": True, "message": "Đang chờ duyệt."}),
        ),
    ):
        await process_telegram_message(_update("mua thêm 1 cái"), CHAT_ID, graph=graph)

    graph.ainvoke.assert_not_awaited()
    assert send_mock.await_args.args[1] == "Đang chờ duyệt."


@pytest.mark.asyncio
async def test_normal_turn_sends_agent_response_and_dispatches_memory(send_mock):
    graph = _graph(response="Dạ có ạ!")
    with (
        patch("core.telegram.message_handler.AsyncSessionLocal", _session_local()),
        patch(
            "api.dependencies.check_paused_session",
            AsyncMock(return_value={"queued": False}),
        ),
        patch("services.memory.background.post_turn_tasks", AsyncMock()) as post_turn,
    ):
        await process_telegram_message(_update("còn hàng không?"), CHAT_ID, graph=graph)
        await asyncio.sleep(0)  # let the background task start

    assert send_mock.await_args.args == (CHAT_ID, "Dạ có ạ!")
    post_turn.assert_called_once()
    assert post_turn.call_args.kwargs["thread_id"] == f"telegram_{CHAT_ID}"


@pytest.mark.asyncio
async def test_hitl_pause_sends_pending_message_without_memory_dispatch(send_mock):
    graph = _graph(paused=True)
    with (
        patch("core.telegram.message_handler.AsyncSessionLocal", _session_local()),
        patch(
            "api.dependencies.check_paused_session",
            AsyncMock(return_value={"queued": False}),
        ),
        patch("services.memory.background.post_turn_tasks", AsyncMock()) as post_turn,
    ):
        await process_telegram_message(_update("đặt 1 cái"), CHAT_ID, graph=graph)

    assert send_mock.await_args.args == (CHAT_ID, _HITL_PENDING_MESSAGE)
    post_turn.assert_not_called()


@pytest.mark.asyncio
async def test_agent_error_sends_apology(send_mock):
    graph = AsyncMock()
    graph.ainvoke.side_effect = RuntimeError("graph exploded")
    with (
        patch("core.telegram.message_handler.AsyncSessionLocal", _session_local()),
        patch(
            "api.dependencies.check_paused_session",
            AsyncMock(return_value={"queued": False}),
        ),
    ):
        await process_telegram_message(_update("hi"), CHAT_ID, graph=graph)

    assert "error occurred" in send_mock.await_args.args[1]
