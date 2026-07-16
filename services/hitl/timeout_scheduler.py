"""
Why this exists: Background task for HITL timeouts (Article III, V).
What it does: Polls database for sessions paused longer than thresholds.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from core.config import settings
from models.schema import HITLMetadata
from services.hitl.service import HITLService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


logger = logging.getLogger(__name__)

# FR-016: customer-facing warning sent after HITL_TIMEOUT_WARN_MIN minutes paused.
TIMEOUT_WARN_MESSAGE_VI = (
    "⏳ Đơn của bạn đang chờ duyệt từ nhân viên hỗ trợ. "
    "Chúng tôi sẽ phản hồi sớm nhất có thể — cảm ơn bạn đã kiên nhẫn! "
    f"Nếu cần hỗ trợ gấp, vui lòng liên hệ: {settings.SUPPORT_CONTACT_LINK}"
)


def _telegram_chat_id_from_session(session_id: str) -> int | None:
    """Extract Telegram chat_id from a session_id of the form 'telegram_<chat_id>'.

    Sessions from other channels (API/CLI) have no Telegram delivery target.
    Group chat ids are negative, hence the lstrip("-").
    """
    prefix = "telegram_"
    if not session_id.startswith(prefix):
        return None
    suffix = session_id.removeprefix(prefix)
    if suffix.lstrip("-").isdigit():
        return int(suffix)
    return None


async def run_timeout_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    poll_interval_seconds: int = 60,
) -> None:
    """Infinite loop to process HITL timeouts (T047)."""
    logger.info("Starting HITL timeout scheduler...")
    while True:
        try:
            async with session_factory() as db:
                await _process_timeouts(db)
                await db.commit()
        except Exception as e:
            logger.exception(f"Error in timeout scheduler: {e}")

        await asyncio.sleep(poll_interval_seconds)


async def _process_timeouts(db: AsyncSession) -> None:
    """Queries and processes sessions exceeding warn/escalate thresholds (T048, T049)."""
    now = datetime.now(UTC)
    warn_threshold = now - timedelta(minutes=settings.HITL_TIMEOUT_WARN_MIN)
    escalate_threshold = now - timedelta(minutes=settings.HITL_TIMEOUT_ESCALATE_MIN)

    # 1. T048: Handle Warnings (30 min)
    stmt_warn = select(HITLMetadata).where(
        HITLMetadata.status == "paused",
        HITLMetadata.paused_at < warn_threshold,
        HITLMetadata.timeout_notified_at == None,  # noqa: E711
    )
    res_warn = await db.execute(stmt_warn)
    to_warn = res_warn.scalars().all()

    for meta in to_warn:
        logger.warning(
            f"HITL Timeout Warning: Session {meta.session_id} has been paused "
            f"since {meta.paused_at}. Support link: {settings.SUPPORT_CONTACT_LINK}"
        )

        # FR-016: notify the customer for real, not just the server log.
        # Only Telegram-originated sessions carry a deliverable chat_id.
        chat_id = _telegram_chat_id_from_session(meta.session_id)
        notified = False
        if chat_id is not None:
            from services.telegram_service import send_telegram_message

            notified = await send_telegram_message(chat_id, TIMEOUT_WARN_MESSAGE_VI)
            if not notified:
                logger.warning(
                    "HITL timeout warning: Telegram send failed for session %s (chat_id=%s)",
                    meta.session_id,
                    chat_id,
                )
        else:
            logger.warning(
                "HITL timeout warning: session %s has no Telegram chat_id — "
                "customer notification skipped (no delivery channel)",
                meta.session_id,
            )

        # Structured log for observability (T048)
        logger.info(
            "HITL event",
            extra={
                "event": "hitl_timeout_warn",
                "session_id": meta.session_id,
                "paused_at": meta.paused_at.isoformat(),
                "customer_notified": notified,
            },
        )
        meta.timeout_notified_at = now

    # 2. T049: Handle Escalation (60 min)
    stmt_escalate = select(HITLMetadata).where(
        HITLMetadata.status == "paused",
        HITLMetadata.paused_at < escalate_threshold,
    )
    res_escalate = await db.execute(stmt_escalate)
    to_escalate = res_escalate.scalars().all()

    for meta in to_escalate:
        logger.info(
            "HITL event",
            extra={
                "event": "hitl_timeout_escalate",
                "session_id": meta.session_id,
            },
        )
        await HITLService.escalate_to_support(meta.session_id, db)
