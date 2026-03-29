"""Why this exists: Public query endpoint for the AI Sales Agent.
What it does: Accepts user queries and returns RAG-powered answers.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import (
    AsyncSession,  # noqa: TC002 - NEEDED: for Pydantic schema resolution
)

from services.database import get_db
from services.rag import answer_with_rag

router = APIRouter(prefix="/query", tags=["query"])


# ── Pydantic schemas (T022) ──────────────────────────────────────────────────


class QueryRequest(BaseModel):
    """Incoming user query."""

    query: str = Field(..., min_length=1, max_length=2000)
    model: str = Field(default="economy-chat", description="LiteLLM model alias")


class QueryResponse(BaseModel):
    """Structured response from the RAG pipeline."""

    answer: str
    declined: bool
    citations: list[dict[str, Any]]
    best_similarity: float
    query_category: Literal["short", "long", "ambiguous"]
    top_k_used: int
    model_used: str
    escalation_flag: bool
    chunks_before_compression: int
    chunks_after_compression: int


# ── Endpoint (T023) ──────────────────────────────────────────────────────────


@router.post("", response_model=QueryResponse)
async def post_query(
    request: QueryRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> QueryResponse:
    """Why this exists: Main customer-facing RAG query endpoint.
    What it does: Runs the full answer_with_rag pipeline and returns structured output.
    """
    result = await answer_with_rag(db=db, query=request.query, model=request.model)
    return QueryResponse(
        answer=result.answer,
        declined=result.declined,
        citations=result.citations,
        best_similarity=result.best_similarity,
        query_category=result.query_category,
        top_k_used=result.top_k_used,
        model_used=result.model_used,
        escalation_flag=result.escalation_flag,
        chunks_before_compression=result.chunks_before_compression,
        chunks_after_compression=result.chunks_after_compression,
    )
