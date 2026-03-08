"""order_execution_node — finalizes order placement."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.types import Command

if TYPE_CHECKING:
    from core.agent.state import AgentState


async def order_execution_node(state: AgentState) -> Command:
    """Stub: pass-through."""
    return Command(goto="answer_node")
