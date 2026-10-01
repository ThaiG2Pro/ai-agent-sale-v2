"""POST /support/query — Spacely support graph endpoint.

Customer-facing, proxied by Spacely's Next.js route (which owns rate
limiting and customer identity). Guarded by the optional X-Agent-Key.
503 while SUPPORT_GRAPH_ENABLED is false. No HITL pause gateway: the
support graph never interrupts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from openinference.instrumentation import using_attributes
from openinference.semconv.trace import SpanAttributes
from opentelemetry import trace
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import (
    AsyncSession,  # noqa: TC002 - NEEDED: for Pydantic schema resolution
)

from api.dependencies import verify_agent_key
from core.agent.graph import make_agent_config
from core.agent.state import make_initial_state
from services.database import get_db
from services.memory.background import post_turn_tasks

router = APIRouter(prefix="/support", tags=["support"], dependencies=[Depends(verify_agent_key)])
logger = logging.getLogger(__name__)


class SupportQueryRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(..., min_length=1, max_length=255)
    customer_id: str = Field(..., min_length=1, max_length=255)


class SupportCitation(BaseModel):
    name: str


class SupportQueryResponse(BaseModel):
    session_id: str
    answer: str
    declined: bool
    intent: str
    intent_confidence: float
    model_used: str | None
    citations: list[SupportCitation]
    elapsed_ms: float


async def get_support_graph(request: Request) -> Any:
    graph = getattr(request.app.state, "support_graph", None)
    if graph is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Support graph disabled (SUPPORT_GRAPH_ENABLED=false)",
        )
    return graph


@router.post("/query", response_model=SupportQueryResponse)
async def post_support_query(
    request: SupportQueryRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    graph: Annotated[Any, Depends(get_support_graph)],
) -> SupportQueryResponse:
    trace.get_current_span().set_attribute(SpanAttributes.SESSION_ID, request.session_id)
    start = time.time()

    try:
        config = make_agent_config(request.session_id, db=db)
        initial_state = make_initial_state(
            request.message, session_id=request.session_id, customer_id=request.customer_id
        )
        with using_attributes(session_id=request.session_id, user_id=request.customer_id):
            final_state = await graph.ainvoke(initial_state, config=config)
    except Exception as exc:
        logger.exception("support query failed: session=%s", request.session_id)
        raise HTTPException(status_code=500, detail=f"Support agent failed: {exc!s}") from exc

    citations = [
        SupportCitation(name=getattr(c, "name", None) or c.get("name", ""))
        for c in (final_state.get("citations") or [])
        if (getattr(c, "name", None) or (isinstance(c, dict) and c.get("name")))
    ]

    # Memory bookkeeping (summaries, intent log) runs after the reply is sent.
    from services.database import AsyncSessionLocal

    task = asyncio.create_task(
        post_turn_tasks(
            customer_id=request.customer_id,
            thread_id=request.session_id,
            state=final_state,
            db_factory=AsyncSessionLocal,
        )
    )
    task.add_done_callback(
        lambda t: (
            logger.error("support post-turn task failed: %s", t.exception())
            if t.exception()
            else None
        )
    )

    return SupportQueryResponse(
        session_id=request.session_id,
        answer=final_state.get("response") or "",
        declined=bool(final_state.get("declined", False)),
        intent=final_state.get("intent") or "UNKNOWN",
        intent_confidence=float(final_state.get("intent_confidence") or 0.0),
        model_used=final_state.get("model_used"),
        citations=citations,
        elapsed_ms=(time.time() - start) * 1000,
    )
