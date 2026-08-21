"""Unit tests for v3-0 P2 (T13) Telegram HITL admin review callbacks + O27.

Covers: callback parsing, force-reply reason capture, price parsing,
admin-chat guard, clarify-exhausted routing to customer_support_node.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.webhooks import hitl_callbacks
from core.agent.nodes.confidence import _route_after_confidence
from core.agent.state import make_initial_state
from core.config import settings
from core.telegram.models import TelegramUpdate

PAUSE_ID = "01912345-1234-7abc-8def-123456789abc"


def _callback_update(data: str, chat_id: int = 999) -> TelegramUpdate:
    return TelegramUpdate.model_validate(
        {
            "update_id": 1,
            "callback_query": {
                "id": "cbq1",
                "from": {"id": 42, "is_bot": False, "first_name": "Admin"},
                "message": {
                    "message_id": 10,
                    "chat": {"id": chat_id, "type": "private"},
                    "date": 0,
                },
                "data": data,
            },
        }
    )


def _reply_update(reply_text: str, prompt_text: str, chat_id: int = 999) -> TelegramUpdate:
    return TelegramUpdate.model_validate(
        {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "from": {"id": 42, "is_bot": False, "first_name": "Admin"},
                "chat": {"id": chat_id, "type": "private"},
                "date": 0,
                "text": reply_text,
                "reply_to_message": {
                    "message_id": 10,
                    "chat": {"id": chat_id, "type": "private"},
                    "date": 0,
                    "text": prompt_text,
                },
            },
        }
    )


# ---------- price / marker parsing ----------


def test_parse_price_trieu_shorthand():
    assert hitl_callbacks._parse_price("ok 27.5tr nhé") == 27_500_000


def test_parse_price_trieu_word():
    assert hitl_callbacks._parse_price("giảm còn 27 triệu") == 27_000_000


def test_parse_price_raw_number():
    assert hitl_callbacks._parse_price("chốt 27500000") == 27_500_000


def test_parse_price_none_when_absent():
    assert hitl_callbacks._parse_price("không giảm được đâu") is None


def test_case_marker_regex_extracts_pause_and_action():
    m = hitl_callbacks._CASE_MARKER_RE.search(f"✍️ Nhập lý do #case:{PAUSE_ID}:reject xxx")
    assert m is not None
    assert m.group(1) == PAUSE_ID
    assert m.group(2) == "reject"


def test_customer_chat_id_parses_telegram_sessions():
    assert hitl_callbacks._customer_chat_id("telegram_12345") == 12345
    assert hitl_callbacks._customer_chat_id("api_session") is None
    assert hitl_callbacks._customer_chat_id("telegram_notanum") is None


# ---------- admin_chat_id gating ----------


def test_admin_chat_id_none_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "")
    assert hitl_callbacks.admin_chat_id() is None


def test_admin_chat_id_none_when_kill_switch_off(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "999")
    monkeypatch.setattr(settings, "ORDER_HITL_V3_ENABLED", False)
    assert hitl_callbacks.admin_chat_id() is None


def test_admin_chat_id_parses_int(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "999")
    monkeypatch.setattr(settings, "ORDER_HITL_V3_ENABLED", True)
    assert hitl_callbacks.admin_chat_id() == 999


# ---------- handle_hitl_callback ----------


@pytest.mark.asyncio
async def test_non_hitl_callback_not_handled():
    update = _callback_update("retry:inventory_check")
    handled = await hitl_callbacks.handle_hitl_callback(update, AsyncMock(), MagicMock())
    assert handled is False


@pytest.mark.asyncio
async def test_unknown_pause_answers_callback(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "999")
    monkeypatch.setattr(settings, "ORDER_HITL_V3_ENABLED", True)
    db = AsyncMock()
    db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    update = _callback_update(f"hitl:approve:{PAUSE_ID}")
    with patch.object(
        hitl_callbacks, "answer_callback_query", new=AsyncMock(return_value=True)
    ) as ack:
        handled = await hitl_callbacks.handle_hitl_callback(update, db, MagicMock())
    assert handled is True
    ack.assert_awaited_once()
    assert "Case không tồn tại" in ack.await_args.args[1]


@pytest.mark.asyncio
async def test_terminal_pause_is_not_reprocessed(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "999")
    monkeypatch.setattr(settings, "ORDER_HITL_V3_ENABLED", True)
    pause = MagicMock(status="approved", session_id="telegram_123")
    pause.pause_id = uuid.UUID(PAUSE_ID)
    db = AsyncMock()
    db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=pause))
    update = _callback_update(f"hitl:approve:{PAUSE_ID}")
    with (
        patch.object(
            hitl_callbacks, "answer_callback_query", new=AsyncMock(return_value=True)
        ) as ack,
        patch.object(hitl_callbacks.HITLService, "process_approve", new=AsyncMock()) as approve,
    ):
        handled = await hitl_callbacks.handle_hitl_callback(update, db, MagicMock())
    assert handled is True
    approve.assert_not_awaited()
    assert "đã xử lý" in ack.await_args.args[1]


@pytest.mark.asyncio
async def test_counter_button_sends_force_reply_with_marker(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "999")
    monkeypatch.setattr(settings, "ORDER_HITL_V3_ENABLED", True)
    pause = MagicMock(status="paused", session_id="telegram_123")
    pause.pause_id = uuid.UUID(PAUSE_ID)
    db = AsyncMock()
    db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=pause))
    update = _callback_update(f"hitl:counter:{PAUSE_ID}")
    with (
        patch.object(hitl_callbacks, "answer_callback_query", new=AsyncMock(return_value=True)),
        patch.object(
            hitl_callbacks, "send_telegram_html", new=AsyncMock(return_value=True)
        ) as send_html,
    ):
        handled = await hitl_callbacks.handle_hitl_callback(update, db, MagicMock())
    assert handled is True
    send_html.assert_awaited_once()
    prompt = send_html.await_args.args[1]
    assert f"#case:{PAUSE_ID}:counter" in prompt
    assert send_html.await_args.kwargs["force_reply_placeholder"]


@pytest.mark.asyncio
async def test_approve_button_feeds_review_flow_and_notifies_customer(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "999")
    monkeypatch.setattr(settings, "ORDER_HITL_V3_ENABLED", True)
    pause = MagicMock(status="paused", session_id="telegram_123")
    pause.pause_id = uuid.UUID(PAUSE_ID)
    db = AsyncMock()
    db.execute.return_value = MagicMock(
        scalar_one_or_none=MagicMock(side_effect=[pause, 3])  # pause lookup, version lookup
    )
    update = _callback_update(f"hitl:approve:{PAUSE_ID}")
    approve_result = {"status": "resumed", "queue_response": "Đơn đã được duyệt ✅"}
    with (
        patch.object(hitl_callbacks, "answer_callback_query", new=AsyncMock(return_value=True)),
        patch.object(
            hitl_callbacks.HITLService,
            "process_approve",
            new=AsyncMock(return_value=approve_result),
        ) as approve,
        patch.object(
            hitl_callbacks, "send_telegram_message", new=AsyncMock(return_value=True)
        ) as send_msg,
    ):
        handled = await hitl_callbacks.handle_hitl_callback(update, db, MagicMock())
    assert handled is True
    approve.assert_awaited_once()
    payload = approve.await_args.args[0]
    assert payload.action == "approve"
    assert payload.expected_version == 3
    assert payload.admin_user_id == "tg:42"
    # Customer (chat 123) received the graph's response; admin (999) got the ack.
    sent_to = [c.args[0] for c in send_msg.await_args_list]
    assert 999 in sent_to
    assert 123 in sent_to
    customer_msg = next(c.args[1] for c in send_msg.await_args_list if c.args[0] == 123)
    assert customer_msg == "Đơn đã được duyệt ✅"


# ---------- handle_admin_reason_reply ----------


@pytest.mark.asyncio
async def test_reject_reply_requires_reason_and_relays_it(monkeypatch):
    """O27: the admin reason must reach the customer response."""
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "999")
    monkeypatch.setattr(settings, "ORDER_HITL_V3_ENABLED", True)
    pause = MagicMock(status="paused", session_id="telegram_123")
    pause.pause_id = uuid.UUID(PAUSE_ID)
    db = AsyncMock()
    db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(side_effect=[pause, 0]))
    update = _reply_update("hết hàng màu này rồi", f"Nhập lý do #case:{PAUSE_ID}:reject")
    reject_result = {"status": "rejected", "queue_response": None}
    with (
        patch.object(
            hitl_callbacks.HITLService,
            "process_reject",
            new=AsyncMock(return_value=reject_result),
        ) as reject,
        patch.object(
            hitl_callbacks, "send_telegram_message", new=AsyncMock(return_value=True)
        ) as send_msg,
    ):
        handled = await hitl_callbacks.handle_admin_reason_reply(update, db, MagicMock())
    assert handled is True
    reject.assert_awaited_once()
    assert reject.await_args.args[0].reason_or_comment == "hết hàng màu này rồi"
    customer_msg = next(c.args[1] for c in send_msg.await_args_list if c.args[0] == 123)
    assert "hết hàng màu này rồi" in customer_msg  # O27: reason reaches customer


@pytest.mark.asyncio
async def test_counter_reply_without_price_asks_again(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "999")
    monkeypatch.setattr(settings, "ORDER_HITL_V3_ENABLED", True)
    pause = MagicMock(status="paused", session_id="telegram_123")
    pause.pause_id = uuid.UUID(PAUSE_ID)
    db = AsyncMock()
    db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=pause))
    update = _reply_update("giảm chút xíu thôi", f"Nhập giá #case:{PAUSE_ID}:counter")
    with (
        patch.object(hitl_callbacks.HITLService, "process_approve", new=AsyncMock()) as approve,
        patch.object(
            hitl_callbacks, "send_telegram_message", new=AsyncMock(return_value=True)
        ) as send_msg,
    ):
        handled = await hitl_callbacks.handle_admin_reason_reply(update, db, MagicMock())
    assert handled is True
    approve.assert_not_awaited()
    assert "Không nhận diện được giá" in send_msg.await_args.args[1]


@pytest.mark.asyncio
async def test_counter_reply_with_price_approves_with_override(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "999")
    monkeypatch.setattr(settings, "ORDER_HITL_V3_ENABLED", True)
    pause = MagicMock(status="paused", session_id="telegram_123")
    pause.pause_id = uuid.UUID(PAUSE_ID)
    db = AsyncMock()
    db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(side_effect=[pause, 1]))
    update = _reply_update("chốt 27.5tr nha", f"Nhập giá #case:{PAUSE_ID}:counter")
    with (
        patch.object(
            hitl_callbacks.HITLService,
            "process_approve",
            new=AsyncMock(return_value={"status": "resumed", "queue_response": "OK"}),
        ) as approve,
        patch.object(hitl_callbacks, "send_telegram_message", new=AsyncMock(return_value=True)),
    ):
        handled = await hitl_callbacks.handle_admin_reason_reply(update, db, MagicMock())
    assert handled is True
    payload = approve.await_args.args[0]
    assert payload.approved_price == 27_500_000
    assert payload.reason_or_comment == "chốt 27.5tr nha"


@pytest.mark.asyncio
async def test_plain_admin_message_not_treated_as_reason(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "999")
    monkeypatch.setattr(settings, "ORDER_HITL_V3_ENABLED", True)
    update = TelegramUpdate.model_validate(
        {
            "update_id": 3,
            "message": {
                "message_id": 12,
                "from": {"id": 42, "is_bot": False, "first_name": "Admin"},
                "chat": {"id": 999, "type": "private"},
                "date": 0,
                "text": "hello bot",
            },
        }
    )
    handled = await hitl_callbacks.handle_admin_reason_reply(update, AsyncMock(), MagicMock())
    assert handled is False


# ---------- clarify-exhausted routing (P2.5 / T06) ----------


def test_route_clarify_exhausted_goes_to_customer_support(monkeypatch):
    monkeypatch.setattr(settings, "ORDER_HITL_V3_ENABLED", True)
    state = make_initial_state("cái nào tốt hơn?", "s1", "c1")
    state["intent"] = "INFO_QUERY"
    state["hitl_rejection_reason"] = "clarify_exhausted_still_ambiguous"
    state["risk_signals"] = ["clarify_loop"]
    assert _route_after_confidence(state) == "customer_support_node"


def test_route_stale_rejection_reason_does_not_rehandoff(monkeypatch):
    """A checkpoint-persisted hitl_rejection_reason from a previous turn must
    not re-trigger handoff — risk_signals is reset per turn and gates it."""
    monkeypatch.setattr(settings, "ORDER_HITL_V3_ENABLED", True)
    state = make_initial_state("còn iphone không?", "s1", "c1")
    state["intent"] = "INFO_QUERY"
    state["hitl_rejection_reason"] = "clarify_exhausted_still_ambiguous"
    state["risk_signals"] = []  # reset by make_initial_state each turn
    state["confidence_score"] = 0.9
    state["similarity_score"] = 0.9
    assert _route_after_confidence(state) == "answer_node"


def test_route_clarify_exhausted_respects_kill_switch(monkeypatch):
    monkeypatch.setattr(settings, "ORDER_HITL_V3_ENABLED", False)
    state = make_initial_state("cái nào tốt hơn?", "s1", "c1")
    state["intent"] = "INFO_QUERY"
    state["hitl_rejection_reason"] = "clarify_exhausted_still_ambiguous"
    state["risk_signals"] = ["clarify_loop"]
    state["confidence_score"] = 0.9
    state["similarity_score"] = 0.9
    assert _route_after_confidence(state) == "answer_node"
