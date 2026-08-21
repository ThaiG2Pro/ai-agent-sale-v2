"""Why this exists: Administrative endpoints for RAG management.
What it does: Implements ingestion and search routes secured by X-Admin-Key.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - NEEDED: FastAPI Query annotation resolution
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,  # noqa: TC002 - NEEDED: for Pydantic schema resolution
)

from api.dependencies import verify_admin_key
from models.schema import Product, TextEmbedding
from services.costs import GROUP_BY_CHOICES, cost_report
from services.database import get_db
from services.rag import ingest_product_text, search_products

router = APIRouter(prefix="/admin/rag", tags=["admin"], dependencies=[Depends(verify_admin_key)])

# WP-V2-5: cost dashboard lives under /admin (not /admin/rag) — ops surface,
# not a RAG tool. Same admin-key gate.
costs_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_admin_key)])


class IngestRequest(BaseModel):
    name: str
    sku: str
    description: str
    price: float = 0.0
    metadata: dict[str, Any] | None = None


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=50)


class StatsRequest(BaseModel):
    """Empty request body for stats endpoint."""

    pass


class EmbeddingModelStat(BaseModel):
    name: str
    version: str | None = None
    count: int


class StatsResponse(BaseModel):
    total_products: int
    total_embeddings: int
    embedding_models: list[EmbeddingModelStat]


@router.post("/ingest", status_code=status.HTTP_201_CREATED)
async def admin_ingest(request: IngestRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    """Why this exists: Allows manual ingestion of product data via API."""
    product_id = await ingest_product_text(
        db=db,
        name=request.name,
        sku=request.sku,
        description=request.description,
        price=request.price,
        metadata=request.metadata,
    )
    return {"message": "Ingested successfully", "product_id": str(product_id)}


@router.post("/search")
async def admin_search(request: SearchRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    """Why this exists: Debugging tool to test vector search results."""
    results = await search_products(db=db, query=request.query, top_k=request.top_k)
    return results


@router.post("/stats", response_model=StatsResponse)
async def admin_stats(db: Annotated[AsyncSession, Depends(get_db)]) -> StatsResponse:
    """Returns RAG system statistics for monitoring and debugging.

    Returns:
        - total_products: Count of all products in database
        - total_embeddings: Count of all embeddings generated
        - embedding_models: Breakdown by model name and version
    """
    # Count total products
    product_count_stmt = select(func.count(Product.id))
    total_products = await db.scalar(product_count_stmt)

    # Count total embeddings
    embedding_count_stmt = select(func.count(TextEmbedding.id))
    total_embeddings = await db.scalar(embedding_count_stmt)

    # Get embedding models breakdown
    models_stmt = select(
        TextEmbedding.model_name,
        TextEmbedding.model_version,
        func.count(TextEmbedding.id).label("count"),
    ).group_by(TextEmbedding.model_name, TextEmbedding.model_version)

    models_result = await db.execute(models_stmt)
    models_data = [
        EmbeddingModelStat(
            name=model[0],
            version=model[1],
            count=model[2],
        )
        for model in models_result.all()
    ]

    return StatsResponse(
        total_products=total_products or 0,
        total_embeddings=total_embeddings or 0,
        embedding_models=models_data,
    )


@costs_router.get("/costs")
async def admin_costs(
    db: Annotated[AsyncSession, Depends(get_db)],
    date_from: Annotated[
        datetime | None, Query(alias="from", description="ISO datetime, default: 7 days ago")
    ] = None,
    date_to: Annotated[
        datetime | None, Query(alias="to", description="ISO datetime, default: now")
    ] = None,
    group_by: Annotated[str, Query(description="day | customer | model")] = "day",
) -> dict[str, Any]:
    """WP-V2-5 cost dashboard: aggregate model_traces so the SME can read real
    spend (tokens, USD, latency p50/p95, cache hit-rate) with one curl.

    `group_by=customer` keys on metadata->customer_id (traces written before
    V2-5 lack it and land under "unknown").
    """
    if group_by not in GROUP_BY_CHOICES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"group_by must be one of {list(GROUP_BY_CHOICES)}",
        )
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'from' must be earlier than 'to'",
        )
    try:
        return await cost_report(db, group_by=group_by, date_from=date_from, date_to=date_to)
    except Exception as exc:  # pragma: no cover - defensive wrap, matches memory.py style
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cost report failed",
        ) from exc


@costs_router.get("/metrics")
async def admin_metrics(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """v3-0 P2 (T07/2.3): 80/20 delegation metrics — deflection rate per
    session (target ≥ 0.80) + support-queue depth + paused HITL count.
    Read-only aggregates over existing tables; no new infra.
    """
    from services.metrics import get_agent_metrics

    return await get_agent_metrics(db)
