"""Why this exists: customers often name no product ("cái đó giá sao", "con máy hôm qua em
tư vấn ấy") and expect the agent to dig it out of memory. retrieval_node used to search the
literal vague words (memory was only fetched AFTER retrieval), and the semantic layer only
returned free-text summaries — the structured product lists already stored in episodic events
and conversation summaries were never consulted.
What it does: detects referential queries and gathers recall candidates (product names,
newest first) plus a compact customer digest from episodic events and conversation summaries,
strictly scoped by customer_id.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy import select

from core.config import settings
from models.schema import ConversationSummary, EpisodicEvent
from services.memory.episodic import has_time_reference

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Deictic references to a product the customer does not name ("con đó", "máy này",
# "cái em vừa tư vấn"). Superset of retrieval._PRONOUN_RE: also covers classifiers
# (con/chiếc/máy/em) + demonstratives and "vừa/mới tư vấn" phrasing.
_REFERENTIAL_RE = re.compile(
    r"\b(?:nó"
    r"|(?:cái|con|chiếc|máy|mẫu|sản\s+phẩm|loại|dòng|bản|em)\s+(?:đó|này|kia|ấy|nãy)"
    r"|(?:vừa|mới)\s+(?:tư\s+vấn|nói|gửi|giới\s+thiệu)"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

# Hard cap on candidate names handed back — more is noise for query rewriting.
_MAX_CANDIDATES = 5


def is_referential(query: str | None) -> bool:
    """True when the query points at a product it does not name (pronoun or time reference)."""
    q = query or ""
    return bool(_REFERENTIAL_RE.search(q)) or has_time_reference(q)


def _add(names: list[str], name: str | None) -> None:
    if name and name not in names and len(names) < _MAX_CANDIDATES:
        names.append(name)


async def recall_products(
    *,
    customer_id: str,
    db: AsyncSession,
    exclude_thread_id: str | None = None,
) -> list[str]:
    """Product names this customer discussed, newest first (episodic, then summaries).

    exclude_thread_id skips the current thread — a time-referenced query ("hôm qua")
    points at an EARLIER conversation, not at what was just said in this one.
    Best-effort: returns [] on any DB error.
    """
    if not customer_id:
        return []
    names: list[str] = []
    try:
        if settings.EPISODIC_MEMORY_ENABLED:
            stmt = (
                select(EpisodicEvent.products)
                .where(EpisodicEvent.customer_id == customer_id)
                .order_by(EpisodicEvent.created_at.desc())
                .limit(settings.EPISODIC_RECENT_LIMIT)
            )
            if exclude_thread_id:
                stmt = stmt.where(EpisodicEvent.thread_id != exclude_thread_id)
            for products in (await db.execute(stmt)).scalars().all():
                for p in products or []:
                    _add(names, p.get("name") if isinstance(p, dict) else None)

        stmt = (
            select(ConversationSummary.products_discussed)
            .where(ConversationSummary.customer_id == customer_id)
            .order_by(ConversationSummary.updated_at.desc())
            .limit(3)
        )
        if exclude_thread_id:
            stmt = stmt.where(ConversationSummary.thread_id != exclude_thread_id)
        for products in (await db.execute(stmt)).scalars().all():
            for name in products or []:
                _add(names, name if isinstance(name, str) else None)
    except Exception:
        logger.error("Recall product lookup failed", exc_info=True)
        return names
    return names


async def recall_digest(*, customer_id: str, db: AsyncSession) -> str | None:
    """One memory_context line: products discussed + budget + preference + open questions.

    Built from the structured columns of the latest conversation summaries, which the
    semantic search (summary_text only) never surfaces. None when nothing is known.
    """
    if not customer_id:
        return None
    try:
        stmt = (
            select(ConversationSummary)
            .where(ConversationSummary.customer_id == customer_id)
            .order_by(ConversationSummary.updated_at.desc())
            .limit(3)
        )
        rows = list((await db.execute(stmt)).scalars().all())
    except Exception:
        logger.error("Recall digest lookup failed", exc_info=True)
        return None
    if not rows:
        return None

    products: list[str] = []
    open_questions: list[str] = []
    for row in rows:
        for name in row.products_discussed or []:
            _add(products, name if isinstance(name, str) else None)
        for q in row.open_questions or []:
            if q and q not in open_questions and len(open_questions) < 3:
                open_questions.append(q)
    budget = next((r.budget_stated for r in rows if r.budget_stated), None)
    preference = next((r.customer_preference for r in rows if r.customer_preference), None)

    parts = []
    if products:
        parts.append(f"Sản phẩm khách đã quan tâm (mới nhất trước): {', '.join(products)}")
    if budget:
        parts.append(f"Ngân sách: {budget}")
    if preference:
        parts.append(f"Sở thích: {preference}")
    if open_questions:
        parts.append(f"Câu hỏi còn mở: {'; '.join(open_questions)}")
    return " | ".join(parts) or None
