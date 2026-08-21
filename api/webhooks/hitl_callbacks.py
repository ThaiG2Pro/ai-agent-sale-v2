"""
Why this exists: v3-0 P2 (T13) — admin review of HITL pauses happened only via
the REST /hitl/review endpoint; the Telegram handoff message's inline buttons
had no handler, so admins could see a case but not act on it.
What it does: Handles the `hitl:<action>:<pause_id>` callback queries from the
admin handoff message ([Duyệt]/[Counter]/[Từ chối]/[Xem intent log]) and the
force-reply reason messages for Counter/Từ chối, feeding them into the existing
HITLService review flow. The customer always receives the outcome WITH the
admin's reason (closes FAIL O27).
"""

from __future__ import annotations

import html
import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from sqlalchemy import select

from core.agent.graph import make_agent_config
from core.config import settings
from models.schema import HITLMetadata, InterruptedSession
from services.hitl.admin_notify import format_intent_log_message
from services.hitl.schemas import ReviewActionCreate
from services.hitl.service import HITLService
from services.telegram_service import (
    answer_callback_query,
    send_telegram_html,
    send_telegram_message,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.telegram.models import TelegramUpdate

logger = logging.getLogger(__name__)

# Force-reply prompt marker — the admin's reason arrives as a reply to a
# message containing this. Full pause_id embedded so the reply is self-routing.
_CASE_MARKER_RE = re.compile(r"#case:([0-9a-fA-F-]{36}):(counter|reject)")

_TERMINAL_STATUSES = ("approved", "rejected", "abandoned", "escalated")


def admin_chat_id() -> int | None:
    """The configured admin chat id, or None when the feature is off."""
    raw = settings.TELEGRAM_ADMIN_CHAT_ID
    if not settings.ORDER_HITL_V3_ENABLED or not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _customer_chat_id(session_id: str) -> int | None:
    """Extract the Telegram chat id from a `telegram_{chat_id}` session id."""
    if session_id.startswith("telegram_"):
        try:
            return int(session_id.removeprefix("telegram_"))
        except ValueError:
            return None
    return None


async def _load_pause(db: AsyncSession, pause_id: str) -> HITLMetadata | None:
    try:
        pause_uuid = uuid.UUID(pause_id)
    except ValueError:
        return None
    res = await db.execute(select(HITLMetadata).where(HITLMetadata.pause_id == pause_uuid))
    return res.scalar_one_or_none()


async def _expected_version(db: AsyncSession, session_id: str) -> int:
    res = await db.execute(
        select(InterruptedSession.version).where(InterruptedSession.session_id == session_id)
    )
    return res.scalar_one_or_none() or 0


async def _notify_customer_result(
    session_id: str, queue_response: str | None, fallback: str
) -> None:
    """Send the review outcome to the customer chat (O27: reason always included)."""
    chat_id = _customer_chat_id(session_id)
    if chat_id is None:
        return
    await send_telegram_message(chat_id, queue_response or fallback)


async def handle_hitl_callback(update: TelegramUpdate, db: AsyncSession, graph: Any) -> bool:
    """Handle a `hitl:<action>:<pause_id>` inline-button tap. Returns True if handled."""
    cq = update.callback_query
    if cq is None or not cq.data or not cq.data.startswith("hitl:"):
        return False

    parts = cq.data.split(":")
    if len(parts) != 3:
        await answer_callback_query(cq.id, "Callback không hợp lệ")
        return True
    _, action, pause_id = parts
    admin_chat = admin_chat_id()
    if admin_chat is None:
        await answer_callback_query(cq.id, "HITL v3 đang tắt")
        return True

    pause = await _load_pause(db, pause_id)
    if pause is None:
        await answer_callback_query(cq.id, "Case không tồn tại")
        return True

    if action == "log":
        await answer_callback_query(cq.id)
        await send_telegram_html(
            admin_chat,
            format_intent_log_message(pause.handoff_package or {}, pause_id),
        )
        return True

    if pause.status in _TERMINAL_STATUSES:
        await answer_callback_query(cq.id, f"Case đã xử lý ({pause.status})")
        return True

    if action == "approve":
        await answer_callback_query(cq.id, "Đang duyệt…")
        await _process_review(
            db=db,
            graph=graph,
            pause=pause,
            action="approve",
            admin_user_id=f"tg:{cq.from_.id}",
            admin_chat=admin_chat,
        )
        return True

    if action in ("counter", "reject"):
        # Reason is MANDATORY (T13) — 2-step force-reply flow. The prompt
        # carries the full pause_id + action so the admin's reply self-routes.
        await answer_callback_query(cq.id)
        label = "giá counter + lý do" if action == "counter" else "lý do từ chối"
        await send_telegram_html(
            admin_chat,
            f"✍️ Nhập {label} cho case <code>#case:{html.escape(pause_id)}:{action}</code>\n"
            "(trả lời trực tiếp tin nhắn này — bắt buộc)",
            force_reply_placeholder=f"Nhập {label}…",
        )
        return True

    await answer_callback_query(cq.id, "Hành động không hỗ trợ")
    return True


async def handle_admin_reason_reply(update: TelegramUpdate, db: AsyncSession, graph: Any) -> bool:
    """Handle the admin's force-reply reason for Counter/Từ chối. Returns True if handled."""
    msg = update.message
    admin_chat = admin_chat_id()
    if (
        msg is None
        or admin_chat is None
        or msg.chat.id != admin_chat
        or msg.reply_to_message is None
        or not msg.reply_to_message.text
    ):
        return False

    marker = _CASE_MARKER_RE.search(msg.reply_to_message.text)
    if marker is None:
        return False
    pause_id, action = marker.group(1), marker.group(2)

    reason = (msg.text or "").strip()
    if not reason:
        await send_telegram_message(admin_chat, "Lý do không được để trống — vui lòng nhập lại.")
        return True

    pause = await _load_pause(db, pause_id)
    if pause is None:
        await send_telegram_message(admin_chat, "Case không tồn tại.")
        return True
    if pause.status in _TERMINAL_STATUSES:
        await send_telegram_message(admin_chat, f"Case đã được xử lý ({pause.status}).")
        return True

    admin_user_id = f"tg:{msg.from_.id}" if msg.from_ else "tg:admin"

    if action == "reject":
        await _process_review(
            db=db,
            graph=graph,
            pause=pause,
            action="reject",
            admin_user_id=admin_user_id,
            admin_chat=admin_chat,
            reason=reason,
        )
        return True

    # counter — the admin proposes a price: approve with approved_price override.
    price = _parse_price(reason)
    if price is None:
        await send_telegram_message(
            admin_chat,
            "Không nhận diện được giá trong tin nhắn (ví dụ hợp lệ: '27.5tr còn hàng', "
            "'27500000'). Vui lòng bấm lại nút Counter và nhập lại.",
        )
        return True
    await _process_review(
        db=db,
        graph=graph,
        pause=pause,
        action="approve",
        admin_user_id=admin_user_id,
        admin_chat=admin_chat,
        reason=reason,
        approved_price=price,
    )
    return True


async def _process_review(
    db: AsyncSession,
    graph: Any,
    pause: HITLMetadata,
    action: str,
    admin_user_id: str,
    admin_chat: int,
    reason: str | None = None,
    approved_price: float | None = None,
) -> None:
    """Feed the decision into the existing HITLService review flow + notify both sides."""
    session_id = pause.session_id
    pause_id = str(pause.pause_id)
    payload = ReviewActionCreate(
        session_id=session_id,
        pause_id=pause_id,
        action="reject" if action == "reject" else "approve",
        expected_version=await _expected_version(db, session_id),
        admin_user_id=admin_user_id,
        approved_price=approved_price,
        reason_or_comment=reason,
    )
    # Idempotency key is per (pause, action) — a double-tap replays as a hit.
    idem_key = f"tgcb:{pause_id}:{payload.action}"
    config = make_agent_config(session_id, db=db)
    try:
        if payload.action == "approve":
            result = await HITLService.process_approve(payload, idem_key, db, graph, config)
        else:
            result = await HITLService.process_reject(payload, idem_key, db, graph, config)
        await db.commit()
    except HTTPException as e:
        await db.rollback()
        logger.warning("Telegram HITL review failed for pause %s: %s", pause_id, e.detail)
        await send_telegram_message(
            admin_chat,
            f"⚠️ Không xử lý được case {pause_id[:8]}: {e.detail}",
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Telegram HITL review crashed for pause %s", pause_id)
        await send_telegram_message(admin_chat, f"⚠️ Lỗi hệ thống khi xử lý case {pause_id[:8]}.")
        return

    queue_response = result.get("queue_response") if isinstance(result, dict) else None
    if payload.action == "approve":
        note = (
            f" với giá đề xuất {approved_price:,.0f} đ".replace(",", ".") if approved_price else ""
        )
        await send_telegram_message(admin_chat, f"✅ Đã duyệt case {pause_id[:8]}{note}.")
        fallback = "Đơn hàng của bạn đã được duyệt. Cảm ơn bạn đã chờ!" + (
            f" Ghi chú từ shop: {reason}" if reason else ""
        )
    else:
        await send_telegram_message(admin_chat, f"❌ Đã từ chối case {pause_id[:8]}.")
        # O27: the reason MUST reach the customer even on the fallback path.
        fallback = (
            "Rất tiếc, yêu cầu của bạn chưa được duyệt. "
            f"Lý do từ shop: {reason}. Bạn cần hỗ trợ thêm cứ nhắn shop nhé!"
        )
    await _notify_customer_result(session_id, queue_response, fallback)


def _parse_price(text: str) -> float | None:
    """Parse a VND price from admin free text ('27.5tr', '27 triệu', '27500000')."""
    m_trieu = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:triệu|tr)\b", text.lower())
    if m_trieu:
        try:
            return float(m_trieu.group(1).replace(",", ".")) * 1_000_000
        except ValueError:
            pass
    m_full = re.search(r"\b(\d{6,12})\b", text.replace(".", "").replace(",", ""))
    if m_full:
        try:
            return float(m_full.group(1))
        except ValueError:
            pass
    return None
