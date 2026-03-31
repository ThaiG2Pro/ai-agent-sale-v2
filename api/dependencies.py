"""Why this exists: Provides shared dependencies for FastAPI routes.
What it does: Implements X-Admin-Key security check for administrative endpoints.
"""

from __future__ import annotations

from secrets import compare_digest
from typing import TYPE_CHECKING, Any

import logfire
from fastapi import Header, HTTPException, Request, status

from core.agent.graph import build_graph
from core.config import settings
from models.schema import HITLMetadata
from services.hitl.service import HITLService

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph
    from sqlalchemy.ext.asyncio import AsyncSession


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


async def verify_telegram_secret(
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
) -> None:
    """Validate Telegram webhook secret token using constant-time comparison."""
    expected_secret = settings.TELEGRAM_WEBHOOK_SECRET
    provided_secret = x_telegram_bot_api_secret_token or ""
    if not provided_secret:
        logfire.warn("Telegram webhook rejected: missing secret header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret",
        )
    if not expected_secret or not compare_digest(provided_secret, expected_secret):
        logfire.warn("Telegram webhook rejected: invalid secret token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret",
        )


async def get_agent_graph(request: Request) -> CompiledStateGraph:
    """
    Why this exists: Provides the compiled LangGraph with persistent checkpointer.
    What it does: Returns the graph cached at startup (compiled once, shared across all
    concurrent sessions — safe because the compiled graph is stateless; all per-session
    state lives in the PostgreSQL checkpointer keyed by thread_id=session_id).
    """
    cached = getattr(request.app.state, "graph", None)
    if cached is not None:
        return cached
    # Fallback for tests / non-lifespan startup paths
    checkpointer = getattr(request.app.state, "checkpointer", None)
    return build_graph(checkpointer=checkpointer)


async def check_paused_session(
    session_id: str,
    message: str,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Why this exists: Intercepts messages for sessions currently in HITL pause (T056).
    What it does: Enqueues message if paused, otherwise allows normal flow.
    Note: Called manually in routes to handle body parameters.
    """
    from sqlalchemy import select

    stmt = select(HITLMetadata).where(
        HITLMetadata.session_id == session_id,
        HITLMetadata.status == "paused",
    )
    res = await db.execute(stmt)
    hitl_meta = res.scalar_one_or_none()

    if hitl_meta:
        # Session is paused, enqueue message and return early response info
        await HITLService.enqueue_message(session_id, message, db)
        return {
            "queued": True,
            "message": "Your message has been received. An agent is reviewing your request.",
        }

    return {"queued": False}
