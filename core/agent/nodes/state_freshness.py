"""state_freshness_validator_node — checks inventory and prices before order execution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.types import Command

if TYPE_CHECKING:
    from core.agent.state import AgentState


async def state_freshness_validator_node(state: AgentState) -> Command:
    """Stub: pass-through."""
    return Command(goto="answer_node")
