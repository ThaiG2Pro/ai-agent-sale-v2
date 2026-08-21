"""Week 3 Agentic Workflow — FastAPI endpoints for LangGraph agent.

Why this exists: Expose the Week 3 LangGraph agent as a RESTful API.
What it does:
  - POST /agent/query — Run single-turn agent interaction
  - POST /agent/stream — Server-sent events (SSE) for streaming agent outputs
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.errors import GraphInterrupt
from openinference.instrumentation import using_attributes
from openinference.semconv.trace import SpanAttributes
from opentelemetry import trace
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import (
    AsyncSession,  # noqa: TC002 - NEEDED: for Pydantic schema resolution
)

from api.dependencies import check_paused_session, get_agent_graph
from core.agent.graph import astream_agent, make_agent_config
from core.agent.state import make_initial_state
from services.database import get_db
from services.memory.background import post_turn_tasks

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger(__name__)


# ── Pydantic schemas ─────────────────────────────────────────────────────────


class AgentQueryRequest(BaseModel):
    """Incoming user query for the Week 3 agent."""

    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(
        default="session-default",
        description="Conversation session ID (for multi-turn, Week 5+)",
    )
    customer_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Cross-session customer identifier (required for memory scoping, Week 5+)",
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
    hitl_paused: bool = Field(
        default=False,
        description="True when graph is paused awaiting admin review (HITL interrupt)",
    )
    hitl_pause_id: str | None = Field(
        default=None,
        description="Pause ID for admin to use in POST /hitl/review",
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
    graph: Annotated[Any, Depends(get_agent_graph)],
) -> Any:
    """Why this exists: Main customer-facing agent query endpoint.
    What it does: Runs the Week 3 LangGraph agent pipeline and returns structured output.

    Execution path: router → {retrieval + escalation + answer} → response
    """
    # ── Observability Trace Tagging (SME Pro 2026) ──
    # Link this request to the conversation session in Phoenix
    current_span = trace.get_current_span()
    current_span.set_attribute(SpanAttributes.SESSION_ID, request.session_id)

    # T056: Paused Session Gateway
    pause_info = await check_paused_session(request.session_id, request.message, db)
    if pause_info["queued"]:
        return AgentQueryResponse(
            session_id=request.session_id,
            message=request.message,
            answer=pause_info["message"],
            intent=IntentInfo(
                primary_intent="FOLLOW_UP",
                confidence=1.0,
                secondary_intents=[],
            ),
            declined=False,
            model_trace=ModelTraceMetadata(
                selected_model="none",
                escalation_flag=False,
                escalation_reason="session_paused",
                similarity_score=0.0,
                confidence_score=0.0,
            ),
            citations=[],
            elapsed_ms=0.0,
            execution_path="paused_gateway",
        )

    start_time = time.time()

    try:
        config = make_agent_config(request.session_id, db=db)
        initial_state = make_initial_state(
            request.message,
            session_id=request.session_id,
            customer_id=request.customer_id,
        )

        # Invoke the agent. using_attributes propagates session.id/user.id onto
        # every OpenInference (LangGraph/LLM) child span — the root FastAPI span
        # attribute alone is invisible to Phoenix's Sessions view.
        with using_attributes(session_id=request.session_id, user_id=request.customer_id):
            final_state = await graph.ainvoke(initial_state, config=config)

        elapsed_ms = (time.time() - start_time) * 1000

        # Extract citations
        citations = []
        if final_state.get("citations"):
            citations = [{"name": c.name, "sku": c.sku} for c in final_state.get("citations", [])]

        # Detect HITL pause: when interrupt() fires, aget_state().next is non-empty
        # containing the interrupted node name. This is the stable LangGraph V1+ API.
        snapshot = await graph.aget_state(config)
        is_hitl_paused = bool(snapshot.next)
        pause_id = final_state.get("hitl_pause_id")

        # If paused and pause_id not in state yet (initial trigger, not resume),
        # extract it from interrupt metadata stored in the snapshot tasks.
        if is_hitl_paused and not pause_id:
            for task in snapshot.tasks or []:
                for intr in getattr(task, "interrupts", None) or []:
                    iv = getattr(intr, "value", None)
                    if isinstance(iv, dict):
                        if "pause_id" in iv:
                            pause_id = iv["pause_id"]
                        if "response" in iv and not final_state.get("response"):
                            final_state["response"] = iv["response"]
                        break
                if pause_id:
                    break

        logger.debug(
            "agent_query result: response=%r, is_hitl_paused=%s",
            final_state.get("response"),
            is_hitl_paused,
        )

        if is_hitl_paused:
            answer = (
                final_state.get("response")
                or "Yêu cầu đặt hàng của bạn đang chờ xác nhận từ nhân viên. "
                "Chúng tôi sẽ phản hồi sớm nhất có thể. Cảm ơn bạn đã kiên nhẫn!"
            )
        else:
            answer = final_state.get("response") or ""

        # Build response
        response = AgentQueryResponse(
            session_id=request.session_id,
            message=request.message,
            answer=answer,
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
            hitl_paused=is_hitl_paused,
            hitl_pause_id=str(pause_id) if pause_id else None,
        )

        # T083: Dispatch background tasks (FR-013) without blocking response
        # Use asyncio.create_task to run in background
        from services.database import AsyncSessionLocal

        # Only dispatch background tasks if not HITL paused (HITL shouldn't trigger memory updates)
        if not is_hitl_paused:
            try:
                # Store task reference to prevent garbage collection
                # (asyncio.create_task alone may let the task be collected if not awaited)
                task = asyncio.create_task(
                    post_turn_tasks(
                        customer_id=request.customer_id,
                        thread_id=request.session_id,
                        state=final_state,
                        db_factory=AsyncSessionLocal,
                    )
                )
                # Add callback to log if task fails (for observability)
                task.add_done_callback(
                    lambda t: (
                        logger.debug(
                            "Background task completed: customer=%s, thread=%s",
                            request.customer_id,
                            request.session_id,
                        )
                        if t.exception() is None
                        else logger.error(
                            "Background task failed: %s",
                            t.exception(),
                        )
                    )
                )
            except Exception as e:
                # Log but don't block response if background task dispatch fails
                logger.error(
                    "Failed to dispatch background tasks: %s (customer=%s, thread=%s)",
                    e,
                    request.customer_id,
                    request.session_id,
                )

        return response

    except GraphInterrupt:
        # Defensive catch: LangGraph normally suppresses GraphInterrupt inside ainvoke,
        # but in rare edge cases (e.g. asyncio task context) it may propagate here.
        # Read the checkpoint snapshot to build the proper HITL pause response.
        try:
            config = {"configurable": {"thread_id": request.session_id, "db": db}}
            snapshot = await graph.aget_state(config)
            pause_id = None
            for task in snapshot.tasks or []:
                for intr in getattr(task, "interrupts", None) or []:
                    iv = getattr(intr, "value", None)
                    if isinstance(iv, dict) and "pause_id" in iv:
                        pause_id = iv["pause_id"]
                        break
                if pause_id:
                    break
        except Exception:
            pause_id = None
        return AgentQueryResponse(
            session_id=request.session_id,
            message=request.message,
            answer=(
                "Yêu cầu đặt hàng của bạn đang chờ xác nhận từ nhân viên. "
                "Chúng tôi sẽ phản hồi sớm nhất có thể. Cảm ơn bạn đã kiên nhẫn!"
            ),
            intent=IntentInfo(
                primary_intent="ORDER_PLACEMENT",
                confidence=0.0,
                secondary_intents=[],
            ),
            declined=False,
            model_trace=ModelTraceMetadata(
                selected_model="unknown",
                escalation_flag=False,
                escalation_reason=None,
                similarity_score=0.0,
                confidence_score=0.0,
            ),
            citations=[],
            elapsed_ms=(time.time() - start_time) * 1000,
            execution_path="hitl_interrupt",
            hitl_paused=True,
            hitl_pause_id=str(pause_id) if pause_id else None,
        )
    except Exception as e:
        msg = f"Agent execution failed: {e!s}"
        raise HTTPException(status_code=500, detail=msg) from e


@router.post("/stream")
async def post_agent_stream(
    request: AgentQueryRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    graph: Annotated[Any, Depends(get_agent_graph)],
):
    """Why this exists: SSE endpoint for streaming agent node execution (FR-006).
    What it does: Streams NodeStreamEvent objects as the agent executes each node.

    Per-node events include: node_name, state_snapshot (delta), timestamp (ISO).
    Client receives Server-Sent Events stream, each event is a JSON object.
    """
    # ── Observability Trace Tagging (SME Pro 2026) ──
    # Link this request to the conversation session in Phoenix
    current_span = trace.get_current_span()
    current_span.set_attribute(SpanAttributes.SESSION_ID, request.session_id)

    # T056: Paused Session Gateway
    pause_info = await check_paused_session(request.session_id, request.message, db)
    if pause_info["queued"]:

        async def generate_paused_event():
            from datetime import UTC, datetime

            timestamp = datetime.now(UTC).isoformat()
            msg = pause_info["message"]
            data = (
                f'{{"node_name": "gateway", '
                f'"state_snapshot": {{"response": "{msg}"}}, '
                f'"timestamp": "{timestamp}"}}'
            )
            yield f"data: {data}\n\n"

        return StreamingResponse(generate_paused_event(), media_type="text/event-stream")

    async def generate_events():
        """Generator yielding SSE-formatted NodeStreamEvent objects."""
        try:
            # Use astream_agent which handles event streaming internally.
            # using_attributes propagates session.id/user.id onto the
            # OpenInference spans created inside the stream (Sessions view).
            with using_attributes(session_id=request.session_id, user_id=request.customer_id):
                async for event in astream_agent(
                    request.message,
                    session_id=request.session_id,
                    customer_id=request.customer_id,
                    db=db,
                    graph=graph,
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


@router.get("/session/{session_id}/history")
async def get_session_history(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    graph: Annotated[Any, Depends(get_agent_graph)],
) -> dict[str, Any]:
    """Retrieves conversation history and graph state snapshot for a session ID.
    Used by local test UI when resuming a session.
    """
    config = make_agent_config(session_id, db=db)
    try:
        snapshot = await graph.aget_state(config)
        state_values = snapshot.values if snapshot else {}
        messages_raw = state_values.get("messages", [])
        formatted_messages = []
        for msg in messages_raw:
            role = getattr(msg, "type", None) or getattr(msg, "role", "unknown")
            content = getattr(msg, "content", str(msg))
            if role == "human":
                role = "user"
            elif role == "ai":
                role = "assistant"
            formatted_messages.append({"role": role, "content": content})

        # Safeguard: if state_values has response but messages doesn't end with assistant response, append it
        resp_text = state_values.get("response")
        if resp_text and (
            not formatted_messages
            or formatted_messages[-1]["role"] != "assistant"
            or formatted_messages[-1]["content"] != resp_text
        ):
            formatted_messages.append({"role": "assistant", "content": resp_text})

        is_hitl_paused = bool(snapshot.next) if snapshot else False
        pause_id = state_values.get("hitl_pause_id")
        if is_hitl_paused and not pause_id and snapshot:
            for task in snapshot.tasks or []:
                for intr in getattr(task, "interrupts", None) or []:
                    iv = getattr(intr, "value", None)
                    if isinstance(iv, dict) and "pause_id" in iv:
                        pause_id = iv["pause_id"]
                        break
                if pause_id:
                    break

        return {
            "session_id": session_id,
            "exists": bool(state_values),
            "messages": formatted_messages,
            "order_info": state_values.get("order_info"),
            "customer_id": state_values.get("customer_id"),
            "hitl_paused": is_hitl_paused,
            "hitl_pause_id": str(pause_id) if pause_id else None,
            "next_node": snapshot.next[0] if (snapshot and snapshot.next) else None,
        }
    except Exception as e:
        logger.exception("Failed to load session history for %s: %s", session_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to load session history: {e}") from e
