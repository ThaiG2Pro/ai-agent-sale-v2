"""
Why this exists: REST endpoints for Human-in-the-Loop (HITL) admin operations.
What it does: Provides state inspection and review submission for paused sessions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import ValidationError

from api.dependencies import get_agent_graph, verify_admin_key
from models.schema import HITLMetadata
from services.database import get_db
from services.hitl.service import HITLService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from services.hitl.schemas import ReviewActionCreate

router = APIRouter(prefix="/hitl", tags=["hitl"])
logger = logging.getLogger(__name__)


@router.get("/session/{session_id}/state", dependencies=[Depends(verify_admin_key)])
async def get_paused_state(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    graph: Annotated[Any, Depends(get_agent_graph)],
) -> dict[str, Any]:
    """Retrieves current paused state for admin review (T050)."""
    config = {"configurable": {"thread_id": session_id}}
    try:
        return await HITLService.get_session_state(session_id, graph, config, db)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get state for session {session_id}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/review", dependencies=[Depends(verify_admin_key)])
async def submit_review(
    payload: ReviewActionCreate,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    graph: Annotated[Any, Depends(get_agent_graph)],
    x_idempotency_key: Annotated[str, Header(alias="X-Idempotency-Key")],
) -> dict[str, Any]:
    """Submit admin decision on a paused session (T051, T052)."""
    config = {"configurable": {"thread_id": payload.session_id, "db": db}}

    # T052: (1) Terminal status gate
    # We query the status before calling service methods to ensure it's not already resolved.
    from sqlalchemy import select

    stmt = select(HITLMetadata).where(HITLMetadata.pause_id == payload.pause_id).limit(1)
    res = await db.execute(stmt)
    hitl_meta = res.scalar_one_or_none()

    if not hitl_meta:
        raise HTTPException(status_code=404, detail="No active HITL pause for session")

    if hitl_meta.status in ["approved", "rejected", "abandoned", "escalated"]:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Session already resolved",
                "status": hitl_meta.status,
                "assigned_to": hitl_meta.admin_id,
            },
        )

    try:
        if payload.action == "approve":
            result = await HITLService.process_approve(
                payload, x_idempotency_key, db, graph, config
            )
        elif payload.action == "reject":
            result = await HITLService.process_reject(
                payload, x_idempotency_key, db, graph, config
            )
        elif payload.action == "request_edit":
            result = await HITLService.process_request_edit(
                payload, x_idempotency_key, db, graph, config
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported action: {payload.action}")

        # Set idempotency status header
        if result.get("status") == "hit":
            response.headers["X-Idempotency-Status"] = "hit"
        else:
            response.headers["X-Idempotency-Status"] = "new"
            await db.commit()  # Commit transaction if successful and new

        return result

    except HTTPException:
        # Re-raise already formed HTTP exceptions (like 409 conflict from service)
        raise
    except ValidationError as e:
        # T052: (4) Catch Pydantic ValidationError
        raise HTTPException(status_code=422, detail=e.errors()) from e
    except Exception as e:
        logger.exception(f"Review processing failed for session {payload.session_id}")
        raise HTTPException(status_code=500, detail=f"Internal error: {e!s}") from e
