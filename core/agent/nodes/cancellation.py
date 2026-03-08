"""cancellation_node — processes order cancellation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.types import Command

if TYPE_CHECKING:
    from core.agent.state import AgentState


async def cancellation_node(state: AgentState) -> Command:
    """Stub: pass-through."""
    return Command(goto="answer_node")
