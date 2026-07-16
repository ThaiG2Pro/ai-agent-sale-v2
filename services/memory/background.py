"""Background tasks for memory operations (Week 5).

Why: Post-turn cleanup, checkpoint monitoring, intent extraction background work.
What: Implements FR-013 lightweight background tasks, checkpoint size warnings (FR-001b).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.agent.state import IntentEnum
from core.config import settings
from models.schema import SalesIntentLog
from services.memory.intent_extractor import SalesIntentExtractor
from services.memory.intent_tracker import IntentTracker

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from core.agent.state import AgentState

logger = logging.getLogger(__name__)


async def check_checkpoint_size(session_id: str, db: AsyncSession) -> int | None:
    """Check checkpoint payload size and warn if excessive (FR-001b, T036).

    Sums octet_length of all checkpoint_blobs rows for the given thread_id
    (the table AsyncPostgresSaver creates in the default schema) and logs a
    WARNING when the total exceeds CHECKPOINT_SIZE_WARN_BYTES.

    Args:
        session_id: LangGraph thread_id
        db: Async database session

    Returns:
        Total blob bytes for the session, or None if no rows / query failed.
    """
    from sqlalchemy import text

    try:
        result = await db.execute(
            text("SELECT SUM(octet_length(blob)) FROM checkpoint_blobs WHERE thread_id = :tid"),
            {"tid": session_id},
        )
        total_bytes = result.scalar_one_or_none()

        if total_bytes is None:
            logger.debug(
                "No checkpoint blobs found for session",
                extra={"session_id": session_id},
            )
            return None

        total_bytes = int(total_bytes)
        if total_bytes > settings.CHECKPOINT_SIZE_WARN_BYTES:
            logger.warning(
                "Checkpoint size exceeds threshold",
                extra={
                    "session_id": session_id,
                    "total_bytes": total_bytes,
                    "warn_threshold_bytes": settings.CHECKPOINT_SIZE_WARN_BYTES,
                },
            )
        else:
            logger.debug(
                "Checkpoint size within threshold",
                extra={"session_id": session_id, "total_bytes": total_bytes},
            )
        return total_bytes

    except Exception as e:
        # Graceful failure - do not block on DB query errors (e.g. checkpointer
        # tables not yet created in this environment)
        logger.debug(
            "Checkpoint size check failed (non-blocking)",
            extra={"session_id": session_id, "error": str(e)},
        )
        return None


async def _maybe_extract_intent(
    customer_id: str,
    thread_id: str,
    state: AgentState,
    db_factory: Callable[[], AsyncSession],
) -> None:
    """Conditionally extract and track sales intent (FR-011, T075).

    Checks if primary intent should skip extraction. If not, calls extractor
    and persists via tracker with optimistic locking.

    Args:
        customer_id: Customer identifier
        thread_id: LangGraph thread_id
        state: Current AgentState with primary_intent and messages
        db_factory: AsyncSession factory for DB operations
    """
    try:
        # NOTE: AgentState's field is "intent" (plain str), not "primary_intent" —
        # using the wrong key here always fell back to IntentEnum.OTHER, which is
        # in the skip list, so extraction never actually ran for any real intent.
        primary_intent = state.get("intent") or IntentEnum.OTHER

        # Check skip list (FR-011: skip FOLLOW_UP, OTHER, SMALLTALK)
        extractor = SalesIntentExtractor()
        if not extractor.should_extract(primary_intent):
            logger.debug(
                "Intent extraction skipped",
                extra={
                    "customer_id": customer_id,
                    "thread_id": thread_id,
                    "reason": f"primary_intent={primary_intent} in skip list",
                },
            )
            return

        # Extract intent from conversation
        conversation_text = "\n".join(
            [
                f"{msg.get('role', 'unknown')}: {msg.get('content', '')}"
                for msg in state.get("messages", [])
            ]
        )

        async with db_factory() as db:
            extraction = await extractor.extract(conversation_text, db)

            # Track the extraction (with optimistic locking)
            tracker = IntentTracker()
            intent_row = await tracker.upsert_with_lock(
                customer_id=customer_id,
                thread_id=thread_id,
                db=db,
                last_intent_model=settings.LIGHT_CHAT_MODEL,
            )

            # Persist the extracted signal detail for audit/reporting (FR-011b).
            # IntentTracking only stores lightweight state (status/version); the
            # actual extracted fields belong in sales_intent_logs, which the
            # GET /memory/intent* endpoints read from.
            db.add(
                SalesIntentLog(
                    customer_id=customer_id,
                    thread_id=thread_id,
                    primary_intent=str(primary_intent),
                    secondary_intents=state.get("secondary_intents") or [],
                    urgency_level=str(extraction.urgency_level),
                    budget_range=extraction.budget_range,
                    product_interest=extraction.product_interest,
                    decision_timeline=extraction.decision_timeline,
                    contact_preference=extraction.contact_preference,
                    extraction_model=settings.LIGHT_CHAT_MODEL,
                    was_skipped=False,
                )
            )

            await db.commit()

            logger.debug(
                "Intent extracted and tracked",
                extra={
                    "customer_id": customer_id,
                    "thread_id": thread_id,
                    "urgency_level": extraction.urgency_level,
                    "intent_tracking_id": str(intent_row.id),
                },
            )

    except Exception as e:
        # FR-013: Graceful degradation - log error, do not re-raise
        logger.error(
            "Intent extraction failed (non-blocking)",
            extra={
                "customer_id": customer_id,
                "thread_id": thread_id,
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )


async def _maybe_summarize(
    customer_id: str,
    thread_id: str,
    state: AgentState,
    db_factory: Callable[[], AsyncSession],
) -> bool:
    """Conditionally summarize conversation (FR-010, T104).

    Checks if summary should be created based on message count.
    If yes, calls summarizer and saves (upserts) to DB.

    Args:
        customer_id: Customer identifier
        thread_id: LangGraph thread_id
        state: Current AgentState with messages
        db_factory: AsyncSession factory for DB operations

    Returns:
        True if a summary was persisted this turn (drives the embed step),
        False when skipped or failed.
    """
    try:
        from services.memory.summarizer import ConversationSummarizer

        message_count = len(state.get("messages", []))
        has_existing_summary = state.get("thread_summary_exists", False)
        messages_since = len(state.get("messages", [])) - (
            state.get("last_summary_message_count", 0)
        )

        # Check if summary should be created (T093-T096)
        if not ConversationSummarizer.should_summarize(
            message_count=message_count,
            has_existing_summary=has_existing_summary,
            messages_since_last_summary=messages_since,
        ):
            logger.debug(
                "Summarization skipped (below threshold)",
                extra={
                    "customer_id": customer_id,
                    "thread_id": thread_id,
                    "message_count": message_count,
                },
            )
            return False

        # Summarize conversation (T097-T101)
        messages = state.get("messages", [])
        summarizer = ConversationSummarizer()
        summary = await summarizer.summarize(messages, session_id=thread_id)

        # Save to DB — save_summary upserts and commits (T102-T103)
        async with db_factory() as db:
            await ConversationSummarizer.save_summary(
                summary=summary,
                session_id=thread_id,
                customer_id=customer_id,
                turn_count=message_count,
                db=db,
            )

        logger.debug(
            "Conversation summarized",
            extra={
                "customer_id": customer_id,
                "thread_id": thread_id,
                "summary_text_length": len(summary.summary_text or ""),
                "products": summary.products_discussed,
            },
        )
        return True

    except ValueError as e:
        # Expected error for empty conversations
        logger.debug(
            "Summarization failed (empty conversation)",
            extra={
                "customer_id": customer_id,
                "thread_id": thread_id,
                "error": str(e),
            },
        )
        return False
    except Exception as e:
        # FR-013: Graceful degradation - log error, do not re-raise
        logger.error(
            "Summarization failed (non-blocking)",
            extra={
                "customer_id": customer_id,
                "thread_id": thread_id,
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        return False


async def _update_semantic_memory(
    customer_id: str,
    thread_id: str,
    state: AgentState,
    db_factory: Callable[[], AsyncSession],
) -> None:
    """Store conversation summary to semantic memory (T127).

    Called by _summarize_and_embed ONLY after a summary was persisted this
    turn (the old gate on the pre-turn ``thread_summary_exists`` flag was
    always False, so the summarize→embed chain never ran).

    Args:
        customer_id: Customer identifier (for isolation)
        thread_id: LangGraph thread_id
        state: Current AgentState (contains summary data)
        db_factory: AsyncSession factory

    Raises: Nothing (errors logged, not propagated per T128)
    """
    try:
        from sqlalchemy import select

        from services.memory.semantic_memory import SemanticMemoryService

        async with db_factory() as db:
            # Retrieve the latest summary for this customer/thread
            from models.schema import ConversationSummary

            stmt = (
                select(ConversationSummary)
                .where(
                    (ConversationSummary.customer_id == customer_id)
                    & (ConversationSummary.thread_id == thread_id)
                )
                .order_by(ConversationSummary.created_at.desc())
                .limit(1)
            )

            result = await db.execute(stmt)
            latest_summary = result.scalar_one_or_none()

            if not latest_summary:
                logger.warning("No latest summary found; skipping semantic memory store")
                return

            semantic_service = SemanticMemoryService()

            # Store summary to semantic memory with embeddings
            await semantic_service.store(
                summary_id=str(latest_summary.id),
                customer_id=customer_id,
                session_id=thread_id,
                summary_text=latest_summary.summary_text or "",
                db=db,
            )

            logger.info(
                "Semantic memory updated",
                extra={
                    "customer_id": customer_id,
                    "summary_id": str(latest_summary.id),
                },
            )

    except Exception as e:
        # T128: Log error, don't propagate (graceful degradation)
        logger.error(
            "Semantic memory update failed (non-blocking)",
            extra={
                "customer_id": customer_id,
                "thread_id": thread_id,
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )


async def _summarize_and_embed(
    customer_id: str,
    thread_id: str,
    state: AgentState,
    db_factory: Callable[[], AsyncSession],
) -> None:
    """Chain summarization → semantic memory embedding (T104 + T127).

    The embed step depends on the summary row existing, so it runs
    sequentially after _maybe_summarize instead of in parallel with it —
    and only when a summary was actually persisted this turn.
    """
    summary_saved = await _maybe_summarize(customer_id, thread_id, state, db_factory)
    if not summary_saved:
        logger.debug(
            "Skipping semantic memory update (no summary created)",
            extra={"customer_id": customer_id, "thread_id": thread_id},
        )
        return
    await _update_semantic_memory(customer_id, thread_id, state, db_factory)


async def post_turn_tasks(
    customer_id: str,
    thread_id: str,
    state: AgentState,
    db_factory: Callable[[], AsyncSession],
) -> None:
    """Coordinator for all post-turn background tasks (FR-013, T079).

    Executes 3 memory tasks in parallel:
    1. check_checkpoint_size()
    2. _maybe_extract_intent()
    3. _summarize_and_embed()  (summarize → embed, sequential chain)

    All tasks run via asyncio.gather(return_exceptions=True) so failures
    in one task don't block others. Errors are logged by task index.

    Args:
        customer_id: Customer identifier
        thread_id: LangGraph thread_id
        state: Current AgentState
        db_factory: AsyncSession factory
    """

    async def _checkpoint_task() -> None:
        async with db_factory() as db:
            await check_checkpoint_size(thread_id, db)

    results = await asyncio.gather(
        _checkpoint_task(),
        _maybe_extract_intent(customer_id, thread_id, state, db_factory),
        _summarize_and_embed(customer_id, thread_id, state, db_factory),
        return_exceptions=True,
    )

    # Log any exceptions by task index
    task_names = [
        "checkpoint_size",
        "intent_extraction",
        "summarize_and_embed",
    ]
    for task_idx, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(
                f"Background task {task_names[task_idx]} failed",
                extra={
                    "customer_id": customer_id,
                    "thread_id": thread_id,
                    "task_index": task_idx,
                    "error": str(result),
                    "error_type": type(result).__name__,
                },
            )
