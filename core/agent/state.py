"""
Why this exists: Defines canonical AgentState TypedDict and all Pydantic
boundary models (Article VI) for the LangGraph sales agent.
What it does: Provides typed state, enums, and I/O contracts used by all nodes.
"""

from __future__ import annotations

import operator
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


class HITLReasonEnum(StrEnum):
    """Reasons for HITL pause (Week 4)."""

    ORDER_APPROVAL = "order_approval"
    LOW_CONFIDENCE = "low_confidence"
    COST_LIMIT = "cost_limit"
    REFUND_APPROVAL = "refund_approval"
    STALE_PRICE = "stale_data_price_change"


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

    model_config = ConfigDict(strict=True)


class IntentClassification(BaseModel):
    """Intent classification output with multi-intent support (FR-005, FR-007)."""

    primary_intent: IntentEnum
    secondary_intents: list[IntentEnum] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str

    def has_escalation_intent(self) -> bool:
        """True if ANY intent (primary or secondary) is COMPLAINT or NEGOTIATION."""
        escalation = {IntentEnum.COMPLAINT, IntentEnum.NEGOTIATION}
        return self.primary_intent in escalation or bool(escalation & set(self.secondary_intents))


class EscalationDecision(BaseModel):
    """Escalation node output with model selection."""

    escalate: bool
    reason: EscalationReasonEnum
    selected_model: str


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
    retrieved_chunks: list[dict]
    citations: Annotated[list, operator.add]
    similarity_score: float
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
    estimated_token_cost: int
    order_info: dict | None
    # Retrieval pipeline fields (set by retrieval_node, used by answer_node)
    cached_answer: str | None  # Pre-generated answer from L1/L2 cache hit (skip LLM)
    canonical_query: str | None  # Normalized query text for cache write
    query_vector: list | None  # Embedded query vector for L2 cache write


class NodeStreamEvent(BaseModel):
    """Per-node streaming event (T080, FR-006).

    Emitted by astream_agent for each node completion.
    state_snapshot contains only the delta (fields changed by that node).
    """

    node_name: str
    state_snapshot: dict
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def make_initial_state(user_message: str, session_id: str) -> AgentState:
    """Factory for creating initial AgentState with safe defaults (T054).

    All boolean flags MUST be explicitly False (not None or falsy).
    Ensures no undefined state fields reach the graph.
    """
    return {
        "session_id": session_id,
        "user_message": user_message,
        "messages": [],
        "intent": None,
        "secondary_intents": [],
        "intent_confidence": 0.0,
        "retrieved_chunks": [],
        "citations": [],
        "similarity_score": 0.0,
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
        "hitl_rejection_reason": None,
        "hitl_escalation_count": 0,
        "hitl_approved": False,
        "estimated_token_cost": 0,
        "order_info": None,
        "cached_answer": None,
        "canonical_query": None,
        "query_vector": None,
    }
