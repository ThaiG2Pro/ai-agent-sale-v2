"""
Why this exists: Defines canonical AgentState TypedDict and all Pydantic
boundary models (Article VI) for the LangGraph sales agent.
What it does: Provides typed state, enums, and I/O contracts used by all nodes.
"""

from __future__ import annotations

import operator
import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict


class IntentEnum(StrEnum):
    """Sales intent classification types."""

    INFO_QUERY = "INFO_QUERY"
    PRICING = "PRICING"
    COMPARISON = "COMPARISON"
    COMPLAINT = "COMPLAINT"
    NEGOTIATION = "NEGOTIATION"
    SMALLTALK = "SMALLTALK"
    AVAILABILITY = "AVAILABILITY"
    ORDER_PLACEMENT = "ORDER_PLACEMENT"
    FOLLOW_UP = "FOLLOW_UP"  # Week 5: low-signal follow-up (e.g., "Ok")
    CANCEL = "CANCEL"  # Order cancellation / change mind
    OTHER = "OTHER"  # Week 5: unclassified intent


# Week 5: Intent extraction signal gating (FR-011)
SKIP_INTENT_EXTRACTION: frozenset[IntentEnum] = frozenset(
    {IntentEnum.FOLLOW_UP, IntentEnum.OTHER, IntentEnum.SMALLTALK}
)


class UrgencyLevel(StrEnum):
    """Customer urgency levels extracted from conversation (FR-011b)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class HITLReasonEnum(StrEnum):
    """Reasons for HITL pause (Week 4)."""

    ORDER_APPROVAL = "order_approval"
    LOW_CONFIDENCE = "low_confidence"
    COST_LIMIT = "cost_limit"
    REFUND_APPROVAL = "refund_approval"
    STALE_PRICE = "stale_data_price_change"
    # v3-0 P2 (T06): NEGOTIATION draft at original price awaiting the human's
    # price decision — the agent never counter-offers.
    PRICE_NEGOTIATION = "price_negotiation"


class EscalationReasonEnum(StrEnum):
    """Reasons for model escalation."""

    INTENT_ESCALATION = "intent_escalation"
    LOW_CONFIDENCE = "low_confidence"
    NONE = "none"


class Citation(BaseModel):
    """Citation metadata for grounding RAG answers (Article IX)."""

    product_id: str
    chunk_id: str
    sku: str
    name: str
    source_text: str = Field(
        ...,
        description="Raw chunk text used for grounding (Article IX auditability)",
    )
    # WP-V2-2 (FR-011): source sentence best matching the answer. Optional —
    # None when nothing clears MIN_FRAGMENT_RATIO, absent on pre-V2-2 cache
    # entries. Must exist here or Citation(**cached_dict) in retrieval_node
    # raises on the extra key and the citation is silently dropped.
    fragment_text: str | None = None

    model_config = ConfigDict(strict=True)


class ClarifyingQuestion(BaseModel):
    """One clarifying question for a borderline query (WP-V2-3, clarify loop)."""

    question: str = Field(min_length=1, max_length=500)

    model_config = ConfigDict(strict=True)


class DecomposedQuery(BaseModel):
    """LLM query decomposition output (WP-V2-3). Capped to 3 sub-queries by caller."""

    sub_queries: list[str] = Field(default_factory=list)

    model_config = ConfigDict(strict=True)


class IntentClassification(BaseModel):
    """Intent classification output with multi-intent support (FR-005, FR-007)."""

    primary_intent: IntentEnum
    secondary_intents: list[IntentEnum] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    # v3-0 P1 (T03): classifier reports the LAST message changed intent vs the
    # previous turn. Default False keeps pre-v3-0 payloads/callers valid.
    intent_shift: bool = False

    def has_escalation_intent(self) -> bool:
        """True if ANY intent (primary or secondary) is COMPLAINT or NEGOTIATION."""
        escalation = {IntentEnum.COMPLAINT, IntentEnum.NEGOTIATION}
        return self.primary_intent in escalation or bool(escalation & set(self.secondary_intents))


class EscalationDecision(BaseModel):
    """Escalation node output with model selection."""

    escalate: bool
    reason: EscalationReasonEnum
    selected_model: str


# Week 5: Pydantic models for memory (FR-004, FR-011b)
class ConversationSummaryOutput(BaseModel):
    """Structured summary of a conversation thread (FR-004, FR-011b)."""

    summary_text: str  # Main summary of the conversation
    products_discussed: list[str] = Field(default_factory=list)
    customer_preference: str | None = None
    budget_stated: str | None = None
    open_questions: list[str] = Field(default_factory=list)
    summary_model: str = ""  # e.g., "ollama/qwen3:0.6b" for audit trail (filled later)

    model_config = ConfigDict(strict=True)


class SalesIntentExtraction(BaseModel):
    """Extracted sales intent fields from conversation (FR-011b)."""

    budget_range: str | None = None
    urgency_level: UrgencyLevel = UrgencyLevel.UNKNOWN
    product_interest: list[str] = Field(default_factory=list)
    decision_timeline: str | None = None
    contact_preference: str | None = None

    model_config = ConfigDict(strict=True)


class TraceMetadata(BaseModel):
    """Metadata structure for model_trace.metadata_ JSONB (audit trail)."""

    guard_decision: str  # ACCEPTED|REJECTED
    escalation_reason: str | None
    escalation_failure: bool
    escalation_flag: bool
    intended_model: str | None
    declined: bool
    similarity_score: float
    confidence_score: float

    model_config = ConfigDict(extra="allow")


class AgentState(TypedDict):
    """Canonical state for the LangGraph sales agent (FR-001)."""

    session_id: str
    user_message: str
    messages: Annotated[list, add_messages]  # conversation history
    intent: str | None
    secondary_intents: list[str]
    intent_confidence: float
    # v3-0 P1 (T03): per-turn flag — router detected an intent change vs the
    # previous turn. intent_disagreement_count is cross-turn (persisted by the
    # checkpointer, omitted from make_initial_state): consecutive suppressed
    # disagreements for the sticky-intent escape hatch.
    intent_shift: bool
    intent_disagreement_count: int
    retrieved_chunks: list[dict]
    citations: Annotated[list, operator.add]
    similarity_score: float
    similarity_gap: float
    rerank_score: float | None
    confidence_score: float
    model_used: str | None
    escalation_flag: bool
    escalation_reason: EscalationReasonEnum | None
    escalation_failure: bool
    response: str | None
    declined: bool
    error: str | None
    # HITL fields (Week 4)
    hitl_triggered: bool
    hitl_reason: str | None
    hitl_pause_id: str | None
    hitl_rejection_reason: str | None
    hitl_escalation_count: int
    hitl_approved: bool
    hitl_freshness_valid: bool
    estimated_token_cost: int
    order_info: dict | None
    # SC5: INFO questions queued during HITL pause; answered alongside order confirmation
    pending_info_questions: str | None
    # Retrieval pipeline fields (set by retrieval_node, used by answer_node)
    cached_answer: str | None  # Pre-generated answer from L1/L2 cache hit (skip LLM)
    canonical_query: str | None  # Normalized query text for cache write
    query_vector: list | None  # Embedded query vector for L2 cache write
    # Week 5: Memory fields (FR-001, FR-008, FR-011b)
    customer_id: str  # Cross-session customer identifier (e.g., telegram_user_id)
    memory_context: list[dict]  # Top-K retrieved past summaries [{summary, score, thread_id}, ...]
    memory_retrieval_scores: list[float]  # Cosine similarity scores for each retrieved summary
    thread_summary_exists: bool  # True if a summary exists for this session_id
    sales_intent_skipped: bool  # True if intent extraction was skipped (low-signal turn)
    # WP-V2-3: clarify loop fields
    needs_clarification: bool  # per-turn: confidence_node routes to clarify_node
    awaiting_clarification: bool  # cross-turn: a clarifying question is pending
    clarify_original_query: str | None  # cross-turn: query that triggered the clarify
    clarify_count: int  # cross-turn: clarifies spent on the current original query (max 1)
    # v3-0 P2 (T07): which of the 4 "20%" signals fired this turn — feeds the
    # structured escalate reason in the handoff package. Values:
    # risk_score | intent_negotiation | intent_complaint | clarify_loop | degraded
    risk_signals: list[str]
    # v3-0 P2 (T06): COMPLAINT fact-collection turns spent (cross-turn,
    # omitted from make_initial_state); handoff is mandatory at the quota.
    complaint_turns: int
    # v3-0 P2 (T06): structured note for the human on a NEGOTIATION draft,
    # e.g. "khách xin giảm còn 27.900.000 VND" (per-turn).
    negotiation_note: str | None
    # v3-0 P2 (O27): the admin's approve note — appended to the order
    # confirmation so the reason always reaches the customer.
    hitl_admin_reason: str | None
    # v3-0 P3 (T09): per-turn resilience fields. turn_started_at is a
    # time.monotonic() stamp for the ~30s turn budget; degraded marks a turn
    # answered below the top ladder rung (or by holding+queue).
    turn_started_at: float | None
    degraded: bool
    # v3-0 P4 (T11 4.2): per-turn — the router's conservative keyword gate
    # matched, so answer_node serves the template (no LLM calls this turn).
    smalltalk_fastpath: bool


class NodeStreamEvent(BaseModel):
    """Per-node streaming event (T080, FR-006).

    Emitted by astream_agent for each node completion.
    state_snapshot contains only the delta (fields changed by that node).
    """

    node_name: str
    state_snapshot: dict
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def make_initial_state(user_message: str, session_id: str, customer_id: str) -> AgentState:
    """Factory for creating initial AgentState with safe defaults (T054).

    All boolean flags MUST be explicitly False (not None or falsy).
    Ensures no undefined state fields reach the graph.

    Args:
        user_message: Customer's input message
        session_id: LangGraph thread_id (e.g., "telegram:12345")
        customer_id: Cross-session customer identifier (e.g., "12345")

    Raises:
        ValueError: If customer_id is empty string (no blank identifiers)
    """
    if not customer_id:
        raise ValueError("customer_id cannot be empty (must be non-blank)")

    from langchain_core.messages import HumanMessage

    from core.config import settings

    state: AgentState = {
        "session_id": session_id,
        "user_message": user_message,
        "messages": [HumanMessage(content=user_message)],
        "intent_confidence": 0.0,
        "intent_shift": False,
        "retrieved_chunks": [],
        "citations": [],
        "similarity_score": 0.0,
        "similarity_gap": 0.0,
        "rerank_score": None,
        "confidence_score": 0.0,
        "model_used": None,
        "escalation_flag": False,  # explicit False (FR-007)
        "escalation_reason": None,
        "escalation_failure": False,  # explicit False (FR-007)
        "response": None,
        "declined": False,  # explicit False (SC-001)
        "error": None,
        "hitl_triggered": False,
        "hitl_reason": None,
        "hitl_pause_id": None,
        "hitl_escalation_count": 0,
        "hitl_approved": False,
        "hitl_freshness_valid": False,
        "estimated_token_cost": 0,
        "order_info": None,
        "pending_info_questions": None,
        "cached_answer": None,
        "canonical_query": None,
        "query_vector": None,
        # Week 5: Memory defaults
        "customer_id": customer_id,
        "memory_context": [],
        "memory_retrieval_scores": [],
        "thread_summary_exists": False,
        "sales_intent_skipped": False,
        # v3-0 P2: per-turn signal fields reset every invoke. complaint_turns
        # is cross-turn (checkpointer channel) → deliberately omitted, same
        # pattern as the clarify fields below.
        "risk_signals": [],
        "negotiation_note": None,
        # v3-0 P3 (T09): per-turn — turn budget anchor + degraded flag.
        "turn_started_at": time.monotonic(),
        "degraded": False,
        # v3-0 P4 (T11 4.2): per-turn SMALLTALK fast-path marker.
        "smalltalk_fastpath": False,
        # WP-V2-3: needs_clarification is per-turn → reset every invoke.
        # awaiting_clarification / clarify_original_query / clarify_count are
        # deliberately NOT set here: input keys overwrite checkpointer channels,
        # so including them would wipe the pending-clarify state between turns.
        # Nodes read them with state.get(...) defaults (missing on turn 1).
        "needs_clarification": False,
    }
    # v3-0 P1 (T03): intent / secondary_intents must survive across turns so
    # the router can read previous_intent from the checkpointed state — same
    # omit-the-key pattern as the clarify fields above. Kill switch OFF
    # restores the pre-v3-0 wipe-every-invoke behavior exactly.
    # (intent_disagreement_count is always omitted — it defaults to 0 via
    # state.get() and is only written by the router under the flag.)
    if not settings.INTENT_TRACKING_V3_ENABLED:
        state["intent"] = None
        state["secondary_intents"] = []
    return state
