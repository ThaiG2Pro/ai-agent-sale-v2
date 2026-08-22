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


def _clarify_quota() -> int:
    """Max clarify rounds before handoff/decline (v3-0 P2/T06).

    CLARIFY_MAX_ROUNDS (default 2) applies only when the P2 kill switch is on;
    with ORDER_HITL_V3_ENABLED=False this restores the pre-P2 hard-coded 1.
    """
    if settings.ORDER_HITL_V3_ENABLED:
        return int(settings.CLARIFY_MAX_ROUNDS)
    return 1


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
    user_msg = state.get("user_message", "")

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
    # Anti-loop: clarify_count < quota (v3-0 P2/T06: 2 rounds for in-catalog
    # ambiguity, then handoff; pre-P2: 1 round). Kill switch: CLARIFY_ENABLED=False.
    needs_clarification = False
    borderline_clarify_intents = {"INFO_QUERY", "AVAILABILITY", "COMPARISON"}
    similarity_gap = state.get("similarity_gap", 0.0)
    clarify_count = int(state.get("clarify_count") or 0)
    clarify_quota = _clarify_quota()
    if (
        fused < confidence_threshold
        and settings.CLARIFY_ENABLED
        and intent not in ("ORDER_PLACEMENT", "FOLLOW_UP")
        and not state.get("memory_context")
        and clarify_count < clarify_quota
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

    # v3-0 P2 (T06/T07): in-catalog ambiguity that already spent the clarify
    # quota hands off to a human (the customer is still a lead) instead of
    # declining. Out-of-catalog queries (Layer 1 declined / no citations)
    # never reach here — they keep the polite decline path, no handoff.
    if (
        settings.ORDER_HITL_V3_ENABLED
        and settings.CLARIFY_ENABLED
        and not needs_clarification
        and fused < confidence_threshold
        and clarify_count >= clarify_quota
        and state.get("citations")
        and intent in borderline_clarify_intents
    ):
        return {
            "confidence_score": fused,
            "declined": False,
            "needs_clarification": False,
            "hitl_rejection_reason": "clarify_exhausted_still_ambiguous",
            "risk_signals": [*(state.get("risk_signals") or []), "clarify_loop"],
        }

    # FR-007: INFO_QUERY, PRICING, AVAILABILITY, COMPARISON borderline (0.45 ≤ sim < 0.70)
    # that did NOT trigger clarify must NOT be declined here.
    # _route_after_confidence routes them to escalation_node.
    _borderline_answer_intents = {
        "INFO_QUERY",
        "PRICING",
        "AVAILABILITY",
        "COMPARISON",
        "ORDER_PLACEMENT",
        "FOLLOW_UP",
    }
    if intent in _borderline_answer_intents and is_declined and not needs_clarification:
        is_declined = False  # escalation_node / hitl_guard_node handles execution

    result: dict = {
        "confidence_score": fused,
        "declined": is_declined,
        "needs_clarification": needs_clarification,
    }

    # For ORDER_PLACEMENT: extract order_info from the top citation so it lands
    # in the LangGraph checkpoint BEFORE hitl_guard_node calls interrupt().
    # (interrupt() checkpoints state as-received, not mid-node updates)
    if intent == "ORDER_PLACEMENT" and not state.get("order_info") and state.get("citations"):
        citations = state["citations"]
        # v3-0 P1 (T01 F4/O5): ambiguous ORDER ("đặt cái đó đi" after a vague
        # browse) — top citations are a near-tie, so auto-selecting the first
        # one orders the wrong product. Ask ONE clarifying question instead.
        # Anti-loop: clarify_count < 1 (same guard as WP-V2-3).
        top_name = (
            citations[0].name if hasattr(citations[0], "name") else citations[0].get("name", "")
        ).lower()
        user_named_top_product = any(
            part in user_msg.lower()
            for part in top_name.split()
            if len(part) >= 4
            and part not in ("laptop", "phone", "tai", "nghe", "chuột", "bàn", "phím", "wireless")
        )
        if (
            settings.INTENT_TRACKING_V3_ENABLED
            and settings.CLARIFY_ENABLED
            and len(citations) >= 2
            and similarity_gap <= settings.CLARIFY_SIMILARITY_GAP_MAX
            and not user_named_top_product
        ):
            if clarify_count < clarify_quota:
                result["needs_clarification"] = True
                result["declined"] = False
                return result
            if settings.ORDER_HITL_V3_ENABLED:
                # v3-0 P2 (T06): still a near-tie after the clarify quota —
                # hand off instead of guessing which product to order.
                result["declined"] = False
                result["hitl_rejection_reason"] = "clarify_exhausted_still_ambiguous"
                result["risk_signals"] = [
                    *(state.get("risk_signals") or []),
                    "clarify_loop",
                ]
                return result
        if citations:
            db = cast("AsyncSession", config["configurable"].get("db"))
            top = citations[0]
            product_id = top.product_id if hasattr(top, "product_id") else top.get("product_id")
            sku = top.sku if hasattr(top, "sku") else top.get("sku", "")
            name = top.name if hasattr(top, "name") else top.get("name", "")
            stmt = select(Product).where(Product.id == product_id)
            product_row = (await db.execute(stmt)).scalar_one_or_none()
            price = float(product_row.price) if product_row else 0.0

            user_msg = state.get("user_message", "")
            desc = product_row.description if product_row else ""
            is_match, req_var, avail_var = _check_variant_match(user_msg, name, desc)
            stated_budget = _extract_budget_from_text(user_msg)

            if not is_match:
                price_fmt = f"{price:,.0f} VND".replace(",", ".") if price > 0 else ""
                avail_str = f"bản **{avail_var}**" if avail_var else f"bản **{name}**"
                result["variant_mismatch_msg"] = (
                    f"Xin lỗi, shop hiện không có sẵn phiên bản **{req_var}** cho {name} "
                    f"(shop chỉ có sẵn {avail_str} với giá {price_fmt}). "
                    f"Bạn có muốn đổi sang đặt phiên bản này không ạ?"
                )
            elif stated_budget and price > stated_budget * 1.1:
                price_fmt = f"{price:,.0f} VND".replace(",", ".")
                budget_fmt = f"{stated_budget:,.0f} VND".replace(",", ".")
                result["variant_mismatch_msg"] = (
                    f"Sản phẩm **{name}** hiện có giá niêm yết là **{price_fmt}** "
                    f"(cao hơn mức ngân sách **{budget_fmt}** anh/chị đề xuất). "
                    f"Anh/chị có muốn tham khảo các mẫu sản phẩm khác phù hợp với mức giá {budget_fmt} không ạ?"
                )
            else:
                # Extract dynamic quantity from user message (e.g. "2 chiếc", "3 sp")
                import re as _re

                qty = _extract_quantity_from_text(user_msg)
                phone_match = _re.search(
                    r"(?:sđt|số điện thoại|phone|tel|đt)?\s*(0[35789][0-9]{8})\b",
                    user_msg,
                    _re.IGNORECASE,
                )
                phone = phone_match.group(1) if phone_match else None
                addr_match = _re.search(
                    r"(?:địa chỉ|đc|ở|tại)\s*[:\s]\s*([^,\n]+(?:,[^,\n]+)*)",
                    user_msg,
                    _re.IGNORECASE,
                )
                address = addr_match.group(1).strip() if addr_match else None

                result["order_info"] = {
                    "product_id": str(product_id),
                    "sku": sku,
                    "name": name,
                    "product_name": name,
                    "price": price,
                    "unit_price": price,
                    "approved_price": price,
                    "total_price": price * qty,
                    "quantity": qty,
                    "phone": phone,
                    "address": address,
                    "status": "pending",
                    "items": [
                        {
                            "product_id": str(product_id),
                            "product_name": name,
                            "sku": sku,
                            "quantity": qty,
                            "unit_price": price,
                        }
                    ],
                }

    return result


def _check_variant_match(
    user_msg: str, product_name: str, product_desc: str
) -> tuple[bool, str | None, str | None]:
    """Checks if user requested a specific storage variant (e.g. 256GB) that is not in the catalog product."""
    import re

    msg_lower = user_msg.lower()
    product_all = f"{product_name} {product_desc}".lower()

    storage_matches = re.findall(r"\b(64|128|256|512)\s*(?:gb|g)\b|\b(1|2)\s*tb\b", msg_lower)
    if not storage_matches:
        return True, None, None

    requested_variants = []
    for m in storage_matches:
        val = next(v for v in m if v)
        unit = "TB" if val in ("1", "2") and "tb" in msg_lower else "GB"
        requested_variants.append(f"{val}{unit}".lower())

    for req in requested_variants:
        if req not in product_all:
            avail_match = re.findall(
                r"\b(64|128|256|512)\s*(?:gb|g)\b|\b(1|2)\s*tb\b", product_all
            )
            avail_var = None
            if avail_match:
                aval_val = next(v for v in avail_match[0] if v)
                avail_var = f"{aval_val}GB"
            return False, req.upper(), avail_var

    return True, None, None


def _extract_quantity_from_text(text: str) -> int:
    import re

    if not text:
        return 1
    m_unit = re.search(r"(\d+)\s*(?:chiếc|cái|sp|sản\s+phẩm|bộ|máy|quả|bản)", text.lower())
    if m_unit:
        try:
            val = int(m_unit.group(1))
            if 1 <= val <= 100:
                return val
        except ValueError:
            pass
    m_kw = re.search(r"(?:mua|đặt|lấy|sl|số\s+lượng)\s+(\d+)", text.lower())
    if m_kw:
        try:
            val = int(m_kw.group(1))
            if 1 <= val <= 100:
                return val
        except ValueError:
            pass
    return 1


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

    # v3-0 P1 (F4/O5): an ambiguous ORDER_PLACEMENT clarifies BEFORE the HITL
    # guard — confidence_node only sets this for ORDER when the top citations
    # are a near-tie (and for borderline non-ORDER intents as in WP-V2-3).
    if state.get("needs_clarification"):
        return "clarify_node"

    # v3-0 P2 (T06/T07): clarify quota exhausted on in-catalog ambiguity →
    # hand off to a human instead of declining/guessing. Guarded on the
    # per-turn risk_signals channel (reset each invoke) so a stale
    # hitl_rejection_reason from a previous turn can never re-trigger this.
    if (
        settings.ORDER_HITL_V3_ENABLED
        and state.get("hitl_rejection_reason") == "clarify_exhausted_still_ambiguous"
        and "clarify_loop" in (state.get("risk_signals") or [])
    ):
        return "customer_support_node"

    # Always route ORDER_PLACEMENT through the HITL guard
    if intent == "ORDER_PLACEMENT":
        return "hitl_guard_node"

    # Always route status inquiries or greetings directly to answer_node
    if intent in ("FOLLOW_UP", "SMALLTALK"):
        return "answer_node"

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


def _extract_budget_from_text(text: str) -> float | None:
    """Extracts target budget number (e.g. 15 triệu, 15tr, 15.000.000) from text."""
    import re

    if not text:
        return None
    m_trieu = re.search(r"(\d+(?:\.\d+)?)\s*(?:triệu|tr)\b", text.lower())
    if m_trieu:
        try:
            return float(m_trieu.group(1)) * 1_000_000
        except ValueError:
            pass
    m_full = re.search(r"(\d{1,3}(?:\.\d{3}){2,3})", text)
    if m_full:
        try:
            return float(m_full.group(1).replace(".", ""))
        except ValueError:
            pass
    return None
