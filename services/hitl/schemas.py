"""
Why this exists: Defines Pydantic boundary models for HITL API and service layer (Article VI).
What it does: Provides validation for review actions and queued message classification.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewActionCreate(BaseModel):
    """Schema for POST /hitl/review request (T016)."""

    session_id: str
    pause_id: str
    action: Literal["approve", "reject", "request_edit"]
    expected_version: int
    admin_user_id: str
    state_edits: dict | None = None
    # SC3: admin may override the approved price (e.g. apply a discount) at approval time.
    # Merged into existing order_info rather than replacing it.
    approved_price: float | None = None
    reason_or_comment: str | None = None

    model_config = ConfigDict(strict=True)

    @model_validator(mode="after")
    def validate_action_requirements(self) -> ReviewActionCreate:
        if self.action == "request_edit" and not self.state_edits:
            raise ValueError("state_edits is required when action is 'request_edit'")
        if self.action == "reject" and not self.reason_or_comment:
            raise ValueError("reason_or_comment is required when action is 'reject'")
        # Strip state_edits for approve/reject — it's only meaningful for request_edit.
        # This prevents Swagger example data ({"additionalProp1": {}}) from corrupting
        # the LangGraph checkpoint via aupdate_state.
        if self.action in ("approve", "reject"):
            self.state_edits = None
        return self


class ApprovalPayload(BaseModel):
    """Payload sent to graph.ainvoke(Command(resume=payload)) (T016)."""

    action: Literal["approve", "reject", "request_edit"]
    admin_user_id: str
    state_edits: dict | None = None
    approved_price: float | None = None  # SC3: admin price override at approval time
    reason_or_comment: str | None = None
    acknowledged_message_ids: list[str] = Field(default_factory=list)

    model_config = ConfigDict(strict=True)


class QueueIntentResult(BaseModel):
    """Structured output for single queued message classification (T017)."""

    message_id: str
    text: str
    intent: Literal["CONFIRM", "CANCEL", "MODIFY_ORDER", "NEGOTIATION", "OTHER"]
    confidence: float


class QueuedMessageBatch(BaseModel):
    """Structured output for batch queued message classification (T017)."""

    session_id: str
    messages: list[QueueIntentResult]
    has_cancel: bool = False
    has_modify: bool = False
    has_confirm: bool = False
    has_info: bool = False  # SC5: all queued msgs are INFO_QUERY (questions about product)
    has_qty_change: bool = False  # SC3: qty change only (skip RAG, keep same product)
    has_product_change: bool = False  # SC3: product name change requested (use RAG)
    has_negotiation: bool = False  # NQ2: price negotiation with conditional cancel
    proposed_price: float | None = None  # NQ2: customer's proposed price (e.g. 27.9tr)

    @model_validator(mode="after")
    def compute_flags(self) -> QueuedMessageBatch:
        """Automatically set summary flags based on message intents."""
        intents = {m.intent for m in self.messages}
        self.has_cancel = "CANCEL" in intents
        self.has_modify = "MODIFY_ORDER" in intents
        self.has_confirm = "CONFIRM" in intents
        return self
