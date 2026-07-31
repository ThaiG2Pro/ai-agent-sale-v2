"""Why this exists: The semantic layer stores compressed summaries only — a customer
asking "cái máy hôm qua em tư vấn ấy" cannot be answered from it (WP-V2-4, research §5).
What it does: Append-only episodic event log per consultation turn (write from
answer_node, read newest-first for time-referenced queries), strictly scoped by
customer_id and covered by the RTBF cascade.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy import select

from core.config import settings
from models.schema import EpisodicEvent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Vietnamese (+ a few English) time-reference markers that signal the customer
# is pointing back at a PAST conversation, not asking a fresh question.
_TIME_REFERENCE_RE = re.compile(
    r"(hôm qua|hôm trước|hôm nọ|bữa trước|lần trước|lúc trước|lúc nãy|khi nãy"
    r"|tuần trước|tháng trước|trước đó|đã tư vấn|từng hỏi|đã hỏi"
    r"|yesterday|last time|last week)",
    re.IGNORECASE,
)

# Cap stored response text — episodic rows are recall cues, not transcripts.
_RESPONSE_SUMMARY_MAX_CHARS = 500


def has_time_reference(query: str) -> bool:
    """True when the query references a past conversation by time."""
    return bool(_TIME_REFERENCE_RE.search(query or ""))


class EpisodicMemoryService:
    """Record and recall per-turn episodic events (WP-V2-4)."""

    async def record_event(
        self,
        *,
        customer_id: str,
        thread_id: str,
        user_message: str,
        response: str | None,
        intent: str | None,
        citations: list | None,
        db: AsyncSession,
    ) -> None:
        """Append one episodic event. Best-effort: never raises to the caller.

        Commits its own row — called from answer_node after the trace write,
        off the response hot path.
        """
        if not settings.EPISODIC_MEMORY_ENABLED:
            return
        if not customer_id or not user_message:
            return
        try:
            products = []
            seen: set[str] = set()
            for c in citations or []:
                name = c.name if hasattr(c, "name") else c.get("name")
                sku = c.sku if hasattr(c, "sku") else c.get("sku")
                if name and name not in seen:
                    seen.add(name)
                    products.append({"name": name, "sku": sku})

            db.add(
                EpisodicEvent(
                    customer_id=customer_id,
                    thread_id=thread_id,
                    user_message=user_message[:2000],
                    response_summary=(response or "")[:_RESPONSE_SUMMARY_MAX_CHARS] or None,
                    intent=intent,
                    products=products[:5],
                )
            )
            await db.commit()
        except Exception:
            logger.error("Episodic event write failed", exc_info=True)
            try:
                await db.rollback()
            except Exception:
                pass

    async def recent_events(
        self,
        *,
        customer_id: str,
        db: AsyncSession,
        limit: int | None = None,
    ) -> list[EpisodicEvent]:
        """Newest-first episodic events for ONE customer (strict isolation)."""
        stmt = (
            select(EpisodicEvent)
            .where(EpisodicEvent.customer_id == customer_id)
            .order_by(EpisodicEvent.created_at.desc())
            .limit(limit or settings.EPISODIC_RECENT_LIMIT)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())


def format_event_line(event: EpisodicEvent) -> str:
    """One memory_context line: timestamp + what was asked + products discussed."""
    when = event.created_at.strftime("%d/%m/%Y %H:%M") if event.created_at else "?"
    products = ", ".join(p.get("name", "") for p in (event.products or []) if p.get("name"))
    parts = [f"[{when}] Khách hỏi: {event.user_message}"]
    if products:
        parts.append(f"Sản phẩm đã tư vấn: {products}")
    if event.response_summary:
        parts.append(f"Đã trả lời: {event.response_summary}")
    return " | ".join(parts)
