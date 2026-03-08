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
    reason_or_comment: str | None = None

    model_config = ConfigDict(strict=True)

    @model_validator(mode="after")
    def validate_action_requirements(self) -> ReviewActionCreate:
        if self.action == "request_edit" and not self.state_edits:
            raise ValueError("state_edits is required when action is 'request_edit'")
        if self.action == "reject" and not self.reason_or_comment:
            raise ValueError("reason_or_comment is required when action is 'reject'")
        return self


class ApprovalPayload(BaseModel):
    """Payload sent to graph.ainvoke(Command(resume=payload)) (T016)."""

    action: Literal["approve", "reject", "request_edit"]
    admin_user_id: str
    state_edits: dict | None = None
    reason_or_comment: str | None = None
    acknowledged_message_ids: list[str] = Field(default_factory=list)

    model_config = ConfigDict(strict=True)


class QueueIntentResult(BaseModel):
    """Structured output for single queued message classification (T017)."""

    message_id: str
    text: str
    intent: Literal["CONFIRM", "CANCEL", "MODIFY_ORDER", "OTHER"]
    confidence: float


class QueuedMessageBatch(BaseModel):
    """Structured output for batch queued message classification (T017)."""

    session_id: str
    messages: list[QueueIntentResult]
    has_cancel: bool = False
    has_modify: bool = False
    has_confirm: bool = False

    @model_validator(mode="after")
    def compute_flags(self) -> QueuedMessageBatch:
        """Automatically set summary flags based on message intents."""
        intents = {m.intent for m in self.messages}
        self.has_cancel = "CANCEL" in intents
        self.has_modify = "MODIFY_ORDER" in intents
        self.has_confirm = "CONFIRM" in intents
        return self
