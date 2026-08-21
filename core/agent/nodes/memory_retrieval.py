"""Memory retrieval node - inject past context into system prompt (Phase 7d, T132+).

Why: Customers should see relevant past summaries from previous conversations.
What: Retrieves semantic memory for a customer and injects into system context.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from core.agent.state import AgentState

from core.config import settings

logger = logging.getLogger(__name__)

# v3-0 P4 (T11 4.4): a time-referenced query needs episodic/semantic memory —
# never skip retrieval for these tokens.
_TIME_REFERENCE_TOKENS = (
    "hôm qua",
    "hôm trước",
    "lần trước",
    "bữa trước",
    "tuần trước",
    "tháng trước",
    "đơn cũ",
    "lúc nãy",
    "ban nãy",
    "yesterday",
    "last time",
    "last week",
    "before",
)


def _empty_update() -> dict:
    """Fresh empty state delta (new lists each call — reducers may mutate)."""
    return {"memory_context": [], "memory_retrieval_scores": []}


async def memory_retrieval_node(state: "AgentState", config: "RunnableConfig") -> dict:
    """Retrieve relevant past context for customer (T132).

    Executes semantic memory search and injects top-3 results as memory_context.
    If no customer_id, returns early with empty context (graceful cold-start).

    DB session is injected via config["configurable"]["db"] — LangGraph invokes
    nodes with (state, config), so a (state, db) signature would receive the
    RunnableConfig where the session is expected (P0-1: recall always empty).

    Functional Requirements:
    - FR-009: Retrieve semantic memory for customer context
    - FR-008b: Strict customer_id isolation (no cross-customer leakage)
    - FR-015: Only for non-SMALLTALK intents (T137)

    Args:
        state: Current AgentState (contains customer_id, intent)
        config: RunnableConfig carrying configurable.db

    Returns:
        State update dict with memory_context and memory_retrieval_scores
    """
    try:
        # T133: Missing customer_id → return early with empty context
        customer_id = state.get("customer_id")
        if not customer_id:
            logger.debug("No customer_id in state; skipping memory retrieval")
            return _empty_update()

        # T137: SMALLTALK intents skip memory retrieval (go directly to answer_node)
        from core.agent.state import IntentEnum

        # router_node writes the classification under "intent" (AgentState has no
        # "primary_intent" key); accept both for direct-call tests.
        primary_intent = state.get("primary_intent") or state.get("intent")
        if primary_intent == IntentEnum.SMALLTALK:
            logger.debug(
                "SMALLTALK intent; routing directly to answer_node without memory",
                extra={"customer_id": customer_id},
            )
            return _empty_update()

        # v3-0 P4 (T11 4.4): conditional skip — ONLY on cache-hit or very-high
        # retrieval similarity (the answer is already determined; memory adds
        # latency only). NEVER for FOLLOW_UP or time-referenced queries — this
        # node is what rescues those from borderline confidence.
        if settings.MEMORY_SKIP_ENABLED and primary_intent != IntentEnum.FOLLOW_UP:
            user_msg_l = (state.get("user_message") or "").lower()
            has_time_ref = any(tok in user_msg_l for tok in _TIME_REFERENCE_TOKENS)
            if not has_time_ref and (
                state.get("cached_answer")
                or float(state.get("similarity_score") or 0.0) >= settings.MEMORY_SKIP_SIMILARITY
            ):
                logger.debug("memory skip (4.4): cache-hit/high-similarity turn")
                return _empty_update()

        db = (config.get("configurable") or {}).get("db")
        if db is None:
            logger.debug(
                "No db in config; skipping memory retrieval",
                extra={"customer_id": customer_id},
            )
            return _empty_update()

        # Retrieve semantic memory for this customer
        from services.memory.semantic_memory import SemanticMemoryService

        user_message = state.get("user_message", "")
        semantic_service = SemanticMemoryService()

        # Query semantic memory (T117, T119, T123)
        results = await semantic_service.retrieve(
            customer_id=customer_id,
            query=user_message,
            db=db,
            top_k=settings.MEMORY_TOP_K,  # Adaptive TopK
            min_score=settings.MEMORY_RELEVANCE_THRESHOLD,  # Threshold (T120)
        )

        # T134: Map results to state context format
        memory_context = []
        scores = []
        for result in results:
            memory_context.append(
                {
                    "summary_id": result.summary_id,
                    "summary_text": result.summary_text,
                    "thread_id": result.session_id,  # From result object
                }
            )
            scores.append(result.similarity_score)

        logger.debug(
            "Semantic memory retrieved",
            extra={
                "customer_id": customer_id,
                "results_count": len(memory_context),
                "scores": scores,
            },
        )

        # WP-V2-4: time-referenced queries ("hôm qua", "lần trước") also pull the
        # customer's most recent episodic events — the semantic summaries alone
        # cannot resolve "cái máy hôm qua em tư vấn ấy". Best-effort: an episodic
        # failure must never break the semantic path.
        try:
            from services.memory.episodic import (
                EpisodicMemoryService,
                format_event_line,
                has_time_reference,
            )

            if settings.EPISODIC_MEMORY_ENABLED and has_time_reference(user_message):
                events = await EpisodicMemoryService().recent_events(
                    customer_id=customer_id, db=db
                )
                for event in events:
                    memory_context.append(
                        {
                            "summary_text": format_event_line(event),
                            "thread_id": event.thread_id,
                            "source": "episodic",
                        }
                    )
                    # Recency-selected, not similarity-scored — keep score lists aligned.
                    scores.append(1.0)
                logger.debug(
                    "Episodic memory retrieved",
                    extra={"customer_id": customer_id, "events_count": len(events)},
                )
        except Exception:
            logger.error("Episodic memory retrieval failed", exc_info=True)

        update_dict = {"memory_context": memory_context, "memory_retrieval_scores": scores}
        if memory_context:
            # Past cross-session memory retrieved — allow answer_node to process context
            update_dict["declined"] = False

        return update_dict

    except Exception as e:
        # Graceful error handling: log but don't propagate
        logger.error(
            "Memory retrieval failed",
            exc_info=True,
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        return _empty_update()
