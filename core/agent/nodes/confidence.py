"""Confidence node for LangGraph sales agent (T047-T047b).

Why: Implements dual-layer confidence guard (Layer 1 from RAG, Layer 2 fusion).
Fuses similarity_score + rerank_score, decides whether to proceed or decline.

What: Computes fused confidence_score using AGENT_ALPHA (default 0.7),
applies thresholds, and routes to escalation_node or answer_node.
"""

from __future__ import annotations

from core.agent.state import AgentState  # noqa: TC001 (required for LangGraph type resolution)
from core.config import settings

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


async def confidence_node(state: AgentState) -> dict:
    """Compute fused confidence score and apply Layer 2 guard (T047).

    Three-path logic:
    1. Layer 1 fast-path: If already declined (sim < 0.45) → return immediately
    2. INFO_QUERY borderline: (0.45 ≤ sim < 0.7) → don't decline,
       let routing logic escalate
    3. Normal: Compute fused score, apply 0.70 threshold

    Args:
        state: Current agent state

    Returns:
        State update dict with confidence_score, declined flag
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

    # FR-007: INFO_QUERY, PRICING, AVAILABILITY borderline (0.45 ≤ sim < 0.70) must NOT
    # be declined here. _route_after_confidence will route them to escalation_node.
    # - INFO_QUERY borderline → premium model (complex question, borderline context)
    # - PRICING / AVAILABILITY borderline → economy model (answer with retrieved chunks)
    # Only COMPARISON borderline is declined at Layer 2 (needs high-quality comparison).
    _borderline_answer_intents = {"INFO_QUERY", "PRICING", "AVAILABILITY"}
    if intent in _borderline_answer_intents and is_declined:
        is_declined = False  # escalation_node decides model, answer_node generates

    return {
        "confidence_score": fused,
        "declined": is_declined,
    }


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
    confidence_score = state.get("confidence_score", similarity)

    # Week 4: Always route ORDER_PLACEMENT through the HITL guard
    if intent == "ORDER_PLACEMENT":
        return "hitl_guard_node"

    # INFO_QUERY, PRICING, AVAILABILITY borderline: Layer 1 didn't fire but below
    # Layer 2 threshold → route to escalation_node (which selects premium vs economy)
    _borderline_route_intents = {"INFO_QUERY", "PRICING", "AVAILABILITY"}
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
