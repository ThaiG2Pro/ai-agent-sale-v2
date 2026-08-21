"""
Why this exists: v3-0 P2 (T07/T13) — a human used to receive only a 50-char
`reason` plus a free-form JSONB snapshot, so every handoff meant re-reading
the whole conversation. The delegation contract mandates a 4-part package.
What it does: builds the standardized handoff package at pause/escalation
time: (1) conversation summary + structured escalate reason (which of the
four 20%-signals fired), (2) draft order snapshot, (3) intent log + customer
status, (4) suggested actions for the human (+1 LLM call at escalate time —
accepted by T07; static fallback on any failure). Every part is best-effort:
a package-build failure must never block the pause itself.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from models.schema import ConversationSummary, IntentTracking, SalesIntentLog

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Static fallback when the suggestion LLM call fails — still actionable.
FALLBACK_SUGGESTED_ACTIONS = [
    "Duyệt đơn nếu thông tin sản phẩm/giá/liên hệ đã đầy đủ.",
    "Từ chối hoặc counter kèm lý do cụ thể — lý do sẽ được gửi cho khách.",
]


def _last_messages(state: dict[str, Any], limit: int = 6) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in (state.get("messages") or [])[-limit:]:
        role = getattr(m, "type", None) or getattr(m, "role", "user")
        content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
        if isinstance(content, str) and content.strip():
            out.append({"role": str(role), "content": content[:300]})
    return out


async def _conversation_summary(db: AsyncSession, customer_id: str, thread_id: str) -> str | None:
    row = (
        await db.execute(
            select(ConversationSummary)
            .where(
                ConversationSummary.customer_id == customer_id,
                ConversationSummary.thread_id == thread_id,
            )
            .order_by(ConversationSummary.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row.summary_text if row else None


async def _intent_log(db: AsyncSession, customer_id: str, thread_id: str) -> dict[str, Any]:
    tracking = (
        await db.execute(
            select(IntentTracking)
            .where(IntentTracking.customer_id == customer_id)
            .order_by(IntentTracking.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    latest_log = (
        await db.execute(
            select(SalesIntentLog)
            .where(SalesIntentLog.customer_id == customer_id)
            .order_by(SalesIntentLog.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "customer_status": str(tracking.status) if tracking else "UNKNOWN",
        "latest": (
            {
                "primary_intent": latest_log.primary_intent,
                "secondary_intents": latest_log.secondary_intents,
                "urgency_level": latest_log.urgency_level,
                "budget_range": latest_log.budget_range,
                "product_interest": latest_log.product_interest,
                "decision_timeline": latest_log.decision_timeline,
                "created_at": latest_log.created_at.isoformat(),
            }
            if latest_log
            else None
        ),
        "thread_id": thread_id,
    }


async def _suggested_actions(state: dict[str, Any], reason: dict[str, Any]) -> list[str]:
    """1-2 suggested actions for the human (+1 LLM call — T07 accepts this)."""
    from pydantic import BaseModel, Field

    from services.ai import AIGateway

    class _Suggestions(BaseModel):
        actions: list[str] = Field(default_factory=list, max_length=3)

    order_info = state.get("order_info") or {}
    recent = "\n".join(f"{m['role']}: {m['content']}" for m in _last_messages(state, 4))
    try:
        resp = await AIGateway.complete(
            model="light-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You assist a Vietnamese shop admin reviewing an escalated "
                        "sales conversation. Suggest 1-2 concrete next actions in "
                        "Vietnamese, each with a short justification. "
                        "Respond ONLY with valid JSON matching the schema."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Lý do escalate: {reason}\n"
                        f"Đơn nháp: {order_info.get('name')} x{order_info.get('quantity')} "
                        f"giá {order_info.get('price')}\n"
                        f"Hội thoại gần nhất:\n{recent}"
                    ),
                },
            ],
            response_format=_Suggestions,
        )
        actions = _Suggestions.model_validate_json(resp.choices[0].message.content).actions
        return [a for a in actions if a.strip()][:2] or FALLBACK_SUGGESTED_ACTIONS
    except Exception as exc:
        logger.warning("handoff suggested-actions LLM call failed: %s", exc)
        return FALLBACK_SUGGESTED_ACTIONS


async def build_handoff_package(
    db: AsyncSession,
    state: dict[str, Any],
    *,
    pause_reason: str,
    with_suggestions: bool = True,
) -> dict[str, Any]:
    """Assemble the mandatory 4-part handoff package (T07).

    Never raises — each part degrades independently so the pause itself is
    never blocked by a package-build failure.
    """
    customer_id = str(state.get("customer_id") or "")
    session_id = str(state.get("session_id") or "")

    summary: str | None = None
    try:
        summary = await _conversation_summary(db, customer_id, session_id)
    except Exception:
        logger.warning("handoff: summary lookup failed", exc_info=True)
    recent_messages = _last_messages(state)
    if not summary:
        summary = " | ".join(f"{m['role']}: {m['content']}" for m in recent_messages[-3:]) or (
            state.get("user_message") or ""
        )

    reason = {
        "pause_reason": pause_reason,
        "signals": list(state.get("risk_signals") or []),
        "intent": state.get("intent"),
        "confidence": state.get("confidence_score"),
        "negotiation_note": state.get("negotiation_note"),
    }

    intent_log: dict[str, Any] = {}
    try:
        intent_log = await _intent_log(db, customer_id, session_id)
    except Exception:
        logger.warning("handoff: intent log lookup failed", exc_info=True)

    suggested = FALLBACK_SUGGESTED_ACTIONS
    if with_suggestions:
        suggested = await _suggested_actions(state, reason)

    return {
        "summary": summary,
        "reason": reason,
        "draft_order": state.get("order_info"),
        "intent_log": intent_log,
        "recent_messages": recent_messages,
        "suggested_actions": suggested,
        "built_at": datetime.now(UTC).isoformat(),
    }
