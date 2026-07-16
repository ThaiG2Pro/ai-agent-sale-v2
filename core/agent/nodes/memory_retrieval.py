"""Memory retrieval node - inject past context into system prompt (Phase 7d, T132+).

Why: Customers should see relevant past summaries from previous conversations.
What: Retrieves semantic memory for a customer and injects into system context.
"""

import logging
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.agent.state import AgentState

logger = logging.getLogger(__name__)


async def memory_retrieval_node(state: "AgentState", config: "RunnableConfig") -> "AgentState":
    """Retrieve relevant past context for customer (T132).

    Executes semantic memory search and injects top-3 results as memory_context.
    If no customer_id, returns early with empty context (graceful cold-start).

    Functional Requirements:
    - FR-009: Retrieve semantic memory for customer context
    - FR-008b: Strict customer_id isolation (no cross-customer leakage)
    - FR-015: Only for non-SMALLTALK intents (T137)

    Args:
        state: Current AgentState (contains customer_id, primary_intent)
        config: LangGraph RunnableConfig; the DB session lives at
            config["configurable"]["db"] (P0-1 fix: LangGraph passes config as
            the second positional arg, never a raw db session)

    Returns:
        Updated AgentState with memory_context and memory_retrieval_scores
    """
    try:
        db = cast(
            "AsyncSession | None",
            (config.get("configurable") or {}).get("db"),
        )

        # T133: Missing customer_id → return early with empty context
        customer_id = state.get("customer_id")
        if not customer_id or db is None:
            logger.debug("No customer_id or db in config; skipping memory retrieval")
            state["memory_context"] = []
            state["memory_retrieval_scores"] = []
            return state

        # T137: SMALLTALK intents skip memory retrieval (go directly to answer_node)
        from core.agent.state import IntentEnum

        primary_intent = state.get("primary_intent")
        if primary_intent == IntentEnum.SMALLTALK:
            logger.debug(
                "SMALLTALK intent; routing directly to answer_node without memory",
                extra={"customer_id": customer_id},
            )
            state["memory_context"] = []
            state["memory_retrieval_scores"] = []
            return state

        # Retrieve semantic memory for this customer
        from services.memory.semantic_memory import SemanticMemoryService

        user_message = state.get("user_message", "")
        semantic_service = SemanticMemoryService()

        # Query semantic memory (T117, T119, T123)
        results = await semantic_service.retrieve(
            customer_id=customer_id,
            query=user_message,
            db=db,
            top_k=3,  # Adaptive TopK: start with 3
            min_score=0.75,  # Relevance threshold (T120)
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

        state["memory_context"] = memory_context
        state["memory_retrieval_scores"] = scores

        logger.debug(
            "Semantic memory retrieved",
            extra={
                "customer_id": customer_id,
                "results_count": len(memory_context),
                "scores": scores,
            },
        )

        return state

    except Exception as e:
        # Graceful error handling: log but don't propagate
        logger.error(
            "Memory retrieval failed",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        state["memory_context"] = []
        state["memory_retrieval_scores"] = []
        return state
