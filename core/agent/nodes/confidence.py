"""Confidence node for LangGraph sales agent (T047-T047b).

Why: Implements dual-layer confidence guard (Layer 1 from RAG, Layer 2 fusion).
Fuses similarity_score + rerank_score, decides whether to proceed or decline.

What: Computes fused confidence_score using AGENT_ALPHA (default 0.7),
applies thresholds, and routes to escalation_node or answer_node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import select

from core.agent.state import AgentState  # noqa: TC001 (required for LangGraph type resolution)
from core.config import settings
from models.schema import Product

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from sqlalchemy.ext.asyncio import AsyncSession

# ──────────────────────────────────────────────────────────────────────────
# CONFIDENCE NODE LOGIC (FR-007 + FR-010 interaction)
#
# Path 1: Declined at Layer 1 (sim < 0.45) → skip confidence check,
#         return declined=True
# Path 2: INFO_QUERY borderline (0.45 ≤ sim < 0.7) → don't decline yet,
#         route to escalation_node
# Path 3: Normal (0.45 ≤ sim < 0.7 non-INFO or sim ≥ 0.7) → compute fused score
#         If fused < 0.70 → declined=True (Layer 2 guard)
#         Else → declined=False (proceed to answer_node)
#
# Reference: data-model.md §5 Graph Topology
# ──────────────────────────────────────────────────────────────────────────


async def confidence_node(state: AgentState, config: RunnableConfig) -> dict:
    """Compute fused confidence score and apply Layer 2 guard (T047).

    Three-path logic:
    1. Layer 1 fast-path: If already declined (sim < 0.45) → return immediately
    2. INFO_QUERY borderline: (0.45 ≤ sim < 0.7) → don't decline,
       let routing logic escalate
    3. Normal: Compute fused score, apply 0.70 threshold

    For ORDER_PLACEMENT: also extracts order_info from top citation so it is
    available in state BEFORE hitl_guard_node calls interrupt() (LangGraph
    checkpoints state after a node completes, not mid-node).

    Args:
        state: Current agent state
        config: RunnableConfig (provides DB session)

    Returns:
        State update dict with confidence_score, declined flag, and optionally order_info
    """
    similarity = state.get("similarity_score", 0.0)
    rerank = state.get("rerank_score", None)
    already_declined = state.get("declined", False)

    # Path 1: Layer 1 fast-path — already declined from retrieval
    if already_declined:
        return {
            "confidence_score": similarity,
            "declined": True,
        }

    # Path 2 & 3: Compute fused score
    if rerank is not None:
        # Fusion formula: (1-a)*similarity + a*rerank where a=AGENT_ALPHA (default 0.7)
        alpha = settings.AGENT_ALPHA
        fused = (1 - alpha) * similarity + alpha * rerank
    else:
        # No reranker available (dev mode) — use similarity directly
        fused = similarity

    # Apply Layer 2 threshold
    confidence_threshold = settings.AGENT_CONFIDENCE_THRESHOLD  # 0.70
    intent = state.get("intent", None)
    is_declined = fused < confidence_threshold

    # Do not decline if cross-session memory context exists
    if state.get("memory_context"):
        is_declined = False

    # WP-V3-4: Expand clarify loop to borderline INFO_QUERY, AVAILABILITY, COMPARISON
    # when similarity_gap is small (<= CLARIFY_SIMILARITY_GAP_MAX, default 0.05).
    # PRICING keeps old path (usually specific pricing questions).
    # Anti-loop: clarify_count < 1. Kill switch: CLARIFY_ENABLED=False.
    needs_clarification = False
    borderline_clarify_intents = {"INFO_QUERY", "AVAILABILITY", "COMPARISON"}
    similarity_gap = state.get("similarity_gap", 0.0)
    if (
        fused < confidence_threshold
        and settings.CLARIFY_ENABLED
        and intent != "ORDER_PLACEMENT"
        and not state.get("memory_context")
        and int(state.get("clarify_count") or 0) < 1
    ):
        if intent in borderline_clarify_intents:
            if similarity_gap <= settings.CLARIFY_SIMILARITY_GAP_MAX:
                needs_clarification = True
                is_declined = False
        elif intent == "PRICING":
            needs_clarification = False
        else:
            needs_clarification = True
            is_declined = False

    # FR-007: INFO_QUERY, PRICING, AVAILABILITY, COMPARISON borderline (0.45 ≤ sim < 0.70)
    # that did NOT trigger clarify must NOT be declined here.
    # _route_after_confidence routes them to escalation_node.
    _borderline_answer_intents = {"INFO_QUERY", "PRICING", "AVAILABILITY", "COMPARISON"}
    if intent in _borderline_answer_intents and is_declined and not needs_clarification:
        is_declined = False  # escalation_node decides model, answer_node generates

    result: dict = {
        "confidence_score": fused,
        "declined": is_declined,
        "needs_clarification": needs_clarification,
    }

    # For ORDER_PLACEMENT: extract order_info from the top citation so it lands
    # in the LangGraph checkpoint BEFORE hitl_guard_node calls interrupt().
    # (interrupt() checkpoints state as-received, not mid-node updates)
    if intent == "ORDER_PLACEMENT" and not state.get("order_info"):
        citations = state.get("citations", [])
        if citations:
            db = cast("AsyncSession", config["configurable"].get("db"))
            top = citations[0]
            product_id = top.product_id if hasattr(top, "product_id") else top.get("product_id")
            sku = top.sku if hasattr(top, "sku") else top.get("sku", "")
            name = top.name if hasattr(top, "name") else top.get("name", "")
            stmt = select(Product).where(Product.id == product_id)
            product_row = (await db.execute(stmt)).scalar_one_or_none()
            price = float(product_row.price) if product_row else 0.0
            result["order_info"] = {
                "product_id": str(product_id),
                "sku": sku,
                "name": name,
                "price": price,
                "approved_price": price,
                "quantity": 1,
                "status": "pending",
            }

    return result


def _route_after_confidence(state: AgentState) -> str:
    """Conditional edge from confidence_node (T047b/Week 4).

    Routes to answer_node, escalation_node, or hitl_guard_node based on:
    - INFO_QUERY borderline (0.45 ≤ sim < 0.7, not Layer 1 declined) → escalation_node
    - ORDER_PLACEMENT → hitl_guard_node (always pass through guard)
    - Low confidence for other intents (< 0.7) → hitl_guard_node
    - Otherwise → answer_node (either accepted or declined)
    """
    intent = state.get("intent", None)
    similarity = state.get("similarity_score", 0.0)
    layer1_declined = state.get("declined", False)  # True only if Layer 1 fired
    confidence_threshold = settings.AGENT_CONFIDENCE_THRESHOLD
    # Use computed confidence_score if set by confidence_node (non-zero),
    # else fall back to similarity (unit-test path where confidence_node didn't run).
    confidence_score = state.get("confidence_score") or similarity

    # Week 4: Always route ORDER_PLACEMENT through the HITL guard
    if intent == "ORDER_PLACEMENT":
        return "hitl_guard_node"

    # WP-V2-3: confidence_node flagged a borderline query → ask ONE clarifying
    # question instead of declining (confidence_node never sets this for
    # ORDER_PLACEMENT or when CLARIFY_ENABLED is off).
    if state.get("needs_clarification"):
        return "clarify_node"

    # INFO_QUERY, PRICING, AVAILABILITY, COMPARISON borderline: Layer 1 didn't fire but below
    # Layer 2 threshold → route to escalation_node (which selects premium vs economy)
    _borderline_route_intents = {"INFO_QUERY", "PRICING", "AVAILABILITY", "COMPARISON"}
    if (
        intent in _borderline_route_intents
        and not layer1_declined
        and similarity < confidence_threshold
    ):
        return "escalation_node"

    # Week 4: If not Layer 1 declined but confidence score < threshold for other intents,
    # route to hitl_guard_node for low_confidence guard evaluation.
    if not layer1_declined and confidence_score < confidence_threshold:
        return "hitl_guard_node"

    # Default: route to answer_node (handles both accepted and declined paths)
    return "answer_node"
