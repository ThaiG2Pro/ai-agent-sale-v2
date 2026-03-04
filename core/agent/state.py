"""
Why this exists: Defines canonical AgentState TypedDict and all Pydantic
boundary models (Article VI) for the LangGraph sales agent.
What it does: Provides typed state, enums, and I/O contracts used by all nodes.
"""

from __future__ import annotations

import operator
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
        ..., description="Raw chunk text used for grounding (Article IX auditability)"
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
        return self.primary_intent in escalation or bool(
            escalation & set(self.secondary_intents)
        )


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
