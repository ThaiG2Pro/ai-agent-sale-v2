"""
Why this exists: v3-0 P2 (T13) — HITL pauses previously produced no admin
notification at all; admins had to poll the REST /hitl/pending endpoint.
What it does: formats the 4-part handoff package as ONE Telegram HTML message
(3 sections shown inline: summary+reason, draft snapshot, suggested actions;
the long intent log hides behind a "📋 Xem intent log" callback) with inline
review buttons [✅ Duyệt] [✏️ Counter] [❌ Từ chối] that feed the existing
/hitl/review flow, and sends it to TELEGRAM_ADMIN_CHAT_ID. All best-effort:
a notify failure never blocks the pause.
"""

from __future__ import annotations

import html
import logging
from typing import Any

from core.config import settings

logger = logging.getLogger(__name__)

# Telegram hard limit is 4096 chars/message — keep headroom for HTML tags.
_MAX_MESSAGE_CHARS = 3900

_SIGNAL_LABELS = {
    "risk_score": "điểm rủi ro cao",
    "intent_negotiation": "khách trả giá",
    "intent_complaint": "khách khiếu nại",
    "clarify_loop": "clarify 2 lần vẫn chưa rõ",
    "degraded": "hạ tầng degraded",
}


def _fmt_price(value: Any) -> str:
    try:
        return f"{float(value):,.0f} đ".replace(",", ".")
    except (TypeError, ValueError):
        return "?"


def _draft_lines(draft: dict[str, Any] | None) -> str:
    if not draft:
        return "(không có đơn nháp)"
    items = draft.get("items") or []
    if not items and draft.get("product_id"):
        items = [
            {
                "product_name": draft.get("name") or draft.get("product_name"),
                "quantity": draft.get("quantity", 1),
                "unit_price": draft.get("price"),
            }
        ]
    lines = [
        f"• {html.escape(str(i.get('product_name') or i.get('sku') or 'SP'))} x "
        f"{int(i.get('quantity') or 1)} — {_fmt_price(i.get('unit_price'))}"
        for i in items
    ]
    extra: list[str] = []
    if draft.get("approved_price") is not None and draft.get("approved_price") != draft.get(
        "price"
    ):
        extra.append(f"Giá đề xuất: {_fmt_price(draft['approved_price'])}")
    if draft.get("phone"):
        extra.append(f"SĐT: {html.escape(str(draft['phone']))}")
    if draft.get("address"):
        extra.append(f"Địa chỉ: {html.escape(str(draft['address']))}")
    return "\n".join(lines + extra) or "(không có đơn nháp)"


def format_handoff_message(package: dict[str, Any], pause_id: str, session_id: str) -> str:
    """One HTML message: header, summary+reason, draft snapshot, suggestions.

    The intent log is deliberately NOT inline — it hides behind the
    "📋 Xem intent log" callback (T13 decision 1).
    """
    reason = package.get("reason") or {}
    signals = [_SIGNAL_LABELS.get(s, s) for s in reason.get("signals") or []]
    reason_line = ", ".join(signals) or str(reason.get("pause_reason") or "cần duyệt")
    note = reason.get("negotiation_note")

    parts = [
        f"🔔 <b>CẦN DUYỆT</b> — case <code>{html.escape(pause_id[:8])}</code>",
        f"Khách: <code>{html.escape(session_id)}</code>",
        f"Lý do: {html.escape(reason_line)}"
        + (f" — <i>{html.escape(str(note))}</i>" if note else ""),
        "",
        f"📝 <b>Tóm tắt</b>\n{html.escape(str(package.get('summary') or ''))[:800]}",
        "",
        f"🗒 <b>Đơn nháp</b>\n{_draft_lines(package.get('draft_order'))}",
        "",
        "💡 <b>Gợi ý</b>\n"
        + "\n".join(f"• {html.escape(a)}" for a in package.get("suggested_actions") or []),
    ]
    text = "\n".join(parts)
    if len(text) > _MAX_MESSAGE_CHARS:
        text = text[:_MAX_MESSAGE_CHARS] + "…"
    return text


def format_intent_log_message(package: dict[str, Any], pause_id: str) -> str:
    """Second message sent by the '📋 Xem intent log' callback."""
    log = package.get("intent_log") or {}
    latest = log.get("latest") or {}
    lines = [
        f"📋 <b>Intent log</b> — case <code>{html.escape(pause_id[:8])}</code>",
        f"Trạng thái khách: {html.escape(str(log.get('customer_status', 'UNKNOWN')))}",
    ]
    if latest:
        lines += [
            f"Intent gần nhất: {html.escape(str(latest.get('primary_intent')))}",
            f"Sản phẩm quan tâm: {html.escape(', '.join(latest.get('product_interest') or []))}",
            f"Ngân sách: {html.escape(str(latest.get('budget_range') or '—'))}",
            f"Độ gấp: {html.escape(str(latest.get('urgency_level') or '—'))}",
        ]
    for m in package.get("recent_messages") or []:
        lines.append(f"<i>{html.escape(m['role'])}</i>: {html.escape(m['content'][:200])}")
    text = "\n".join(lines)
    return text[:_MAX_MESSAGE_CHARS]


def handoff_keyboard(pause_id: str) -> dict[str, Any]:
    """Inline buttons feeding the existing /hitl/review flow (T13 decision 2)."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Duyệt", "callback_data": f"hitl:approve:{pause_id}"},
                {"text": "✏️ Counter", "callback_data": f"hitl:counter:{pause_id}"},
                {"text": "❌ Từ chối", "callback_data": f"hitl:reject:{pause_id}"},
            ],
            [{"text": "📋 Xem intent log", "callback_data": f"hitl:log:{pause_id}"}],
        ]
    }


async def notify_admin_handoff(package: dict[str, Any], pause_id: str, session_id: str) -> bool:
    """Send the handoff message + buttons to the admin chat. Never raises."""
    chat_id = settings.TELEGRAM_ADMIN_CHAT_ID
    if not settings.ORDER_HITL_V3_ENABLED or not chat_id:
        return False
    try:
        from services.telegram_service import send_telegram_html

        return await send_telegram_html(
            int(chat_id),
            format_handoff_message(package, pause_id, session_id),
            reply_markup=handoff_keyboard(pause_id),
        )
    except Exception:
        logger.warning("admin handoff notify failed for pause %s", pause_id, exc_info=True)
        return False


async def notify_admin_alert(html_text: str) -> bool:
    """v3-0 P3 (T12 3.5): one-line ops alert to the admin chat. Never raises."""
    chat_id = settings.TELEGRAM_ADMIN_CHAT_ID
    if not chat_id:
        return False
    try:
        from services.telegram_service import send_telegram_html

        return await send_telegram_html(int(chat_id), f"⚠️ <b>ALERT</b> — {html_text}")
    except Exception:
        logger.warning("admin alert notify failed", exc_info=True)
        return False
