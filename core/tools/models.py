"""Pydantic models for tool execution results."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolResult(BaseModel):
    """Structured result for tool execution with retry semantics."""

    success: bool
    data: Any = None
    error: str | None = None
    is_retryable: bool = False
    tool_name: str | None = None
    duration_ms: float | None = Field(default=None, ge=0)

    model_config = ConfigDict(arbitrary_types_allowed=True)
