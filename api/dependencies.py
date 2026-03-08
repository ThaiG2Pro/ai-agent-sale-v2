"""Why this exists: Provides shared dependencies for FastAPI routes.
What it does: Implements X-Admin-Key security check for administrative endpoints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire
from fastapi import Header, HTTPException, Request, status

from core.agent.graph import build_graph
from core.config import settings

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


async def verify_admin_key(x_admin_key: str = Header(None)):
    """
    Why this exists: Secures sensitive administrative endpoints.
    What it does: Compares the provided X-Admin-Key header with the secret.
    """
    if not x_admin_key or x_admin_key != settings.X_ADMIN_KEY:
        logfire.warn("Unauthorized admin access attempt with key: {key}", key=x_admin_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-Key",
        )


async def get_agent_graph(request: Request) -> CompiledStateGraph:
    """
    Why this exists: Provides the compiled LangGraph with persistent checkpointer.
    What it does: Returns graph built with checkpointer stored in app state.
    """
    checkpointer = getattr(request.app.state, "checkpointer", None)
    return build_graph(checkpointer=checkpointer)
