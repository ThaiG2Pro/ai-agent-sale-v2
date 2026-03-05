"""Week 3 Agentic Workflow — FastAPI endpoints for LangGraph agent.

Why this exists: Expose the Week 3 LangGraph agent as a RESTful API.
What it does:
  - POST /agent/query — Run single-turn agent interaction
  - POST /agent/stream — Server-sent events (SSE) for streaming agent outputs
"""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from core.agent.graph import astream_agent, build_graph
from core.agent.state import make_initial_state
from services.database import get_db

router = APIRouter(prefix="/agent", tags=["agent"])


# ── Pydantic schemas ─────────────────────────────────────────────────────────


class AgentQueryRequest(BaseModel):
    """Incoming user query for the Week 3 agent."""

    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(
        default="session-default",
        description="Conversation session ID (for multi-turn, Week 5+)",
    )


class IntentInfo(BaseModel):
    """Intent classification output."""

    primary_intent: str
    confidence: float
    secondary_intents: list[str]


class ModelTraceMetadata(BaseModel):
    """Metadata about model selection and escalation."""

    selected_model: str
    escalation_flag: bool
    escalation_reason: str | None = None
    similarity_score: float
    confidence_score: float


class AgentQueryResponse(BaseModel):
    """Structured response from the Week 3 LangGraph agent."""

    session_id: str
    message: str
    answer: str
    intent: IntentInfo
    declined: bool
    model_trace: ModelTraceMetadata
    citations: list[dict[str, Any]]
    elapsed_ms: float = Field(..., description="Total execution time in milliseconds")
    execution_path: str = Field(
        ...,
        description="Which nodes were executed (router→escalation→answer, etc.)",
    )


class NodeStreamEvent(BaseModel):
    """Per-node streaming event (FR-006)."""

    node_name: str
    state_snapshot: dict[str, Any]
    timestamp: str


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/query", response_model=AgentQueryResponse)
async def post_agent_query(
    request: AgentQueryRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentQueryResponse:
    """Why this exists: Main customer-facing agent query endpoint.
    What it does: Runs the Week 3 LangGraph agent pipeline and returns structured output.

    Execution path: router → {retrieval + escalation + answer} → response
    """
    start_time = time.time()

    try:
        # Build the agent graph with MemorySaver for session checkpointing
        graph = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": request.session_id, "db": db}}
        initial_state = make_initial_state(request.message, session_id=request.session_id)

        # Invoke the agent
        final_state = await graph.ainvoke(initial_state, config=config)

        elapsed_ms = (time.time() - start_time) * 1000

        # Extract citations
        citations = []
        if final_state.get("citations"):
            citations = [{"name": c.name, "sku": c.sku} for c in final_state.get("citations", [])]

        # Build response
        return AgentQueryResponse(
            session_id=request.session_id,
            message=request.message,
            answer=final_state.get("response", ""),
            intent=IntentInfo(
                primary_intent=final_state.get("intent", "UNKNOWN"),
                confidence=final_state.get("intent_confidence", 0.0),
                secondary_intents=final_state.get("secondary_intents", []),
            ),
            declined=final_state.get("declined", False),
            model_trace=ModelTraceMetadata(
                selected_model=final_state.get("model_used") or "declined",
                escalation_flag=final_state.get("escalation_flag", False),
                escalation_reason=final_state.get("escalation_reason"),
                similarity_score=final_state.get("similarity_score", 0.0),
                confidence_score=final_state.get("confidence_score", 0.0),
            ),
            citations=citations,
            elapsed_ms=elapsed_ms,
            execution_path=final_state.get("execution_path", "unknown"),
        )

    except Exception as e:
        msg = f"Agent execution failed: {e!s}"
        raise HTTPException(status_code=500, detail=msg) from e


@router.post("/stream")
async def post_agent_stream(
    request: AgentQueryRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Why this exists: SSE endpoint for streaming agent node execution (FR-006).
    What it does: Streams NodeStreamEvent objects as the agent executes each node.

    Per-node events include: node_name, state_snapshot (delta), timestamp (ISO).
    Client receives Server-Sent Events stream, each event is a JSON object.
    """

    async def generate_events():
        """Generator yielding SSE-formatted NodeStreamEvent objects."""
        try:
            checkpointer = MemorySaver()
            # Use astream_agent which handles event streaming internally
            async for event in astream_agent(
                request.message,
                session_id=request.session_id,
                db=db,
                checkpointer=checkpointer,
            ):
                # astream_agent yields NodeStreamEvent Pydantic models
                # Convert to SSE format: "data: {json}\n\n"
                yield f"data: {event.model_dump_json()}\n\n"

        except Exception as e:
            yield f"event: error\ndata: {{'error': '{e!s}'}}\n\n"

    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
