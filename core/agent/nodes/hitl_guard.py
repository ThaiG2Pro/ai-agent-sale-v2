"""hitl_guard_node — confidence + cost guard; calls interrupt() on threshold breach."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.types import Command

if TYPE_CHECKING:
    from core.agent.state import AgentState


async def hitl_guard_node(state: AgentState) -> Command:
    """Stub: pass-through until T025."""
    return Command(goto="answer_node")
