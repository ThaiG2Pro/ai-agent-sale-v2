"""Confidence node for LangGraph sales agent (T047-T047b).

Why: Implements dual-layer confidence guard (Layer 1 from RAG, Layer 2 fusion).
Fuses similarity_score + rerank_score, decides whether to proceed or decline.

What: Computes fused confidence_score using AGENT_ALPHA (default 0.7),
applies thresholds, and routes to escalation_node or answer_node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings

if TYPE_CHECKING:
    from core.agent.state import AgentState

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
    is_declined = fused < confidence_threshold

    return {
        "confidence_score": fused,
        "declined": is_declined,
    }


def _route_after_confidence(state: AgentState) -> str:
    """Conditional edge from confidence_node (T047b).

    Routes to answer_node or escalation_node based on:
    - INFO_QUERY borderline (0.45 ≤ sim < 0.7) → escalation_node
    - Otherwise → answer_node (either accepted or declined)

    Enables FR-007 escalation for borderline INFO_QUERY cases.
    """
    intent = state.get("intent", None)
    similarity = state.get("similarity_score", 0.0)
    already_declined = state.get("declined", False)
    confidence_threshold = settings.AGENT_CONFIDENCE_THRESHOLD

    # Priority 1: INFO_QUERY borderline escalation
    # Only escalate if: (1) intent is INFO_QUERY, (2) not already declined by Layer 1,
    # (3) similarity is borderline (below Layer 2 threshold)
    if (
        intent == "INFO_QUERY"
        and not already_declined
        and similarity < confidence_threshold
    ):
        return "escalation_node"

    # Default: route to answer_node (handles both accepted and declined paths)
    return "answer_node"
