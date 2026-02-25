"""Why this exists: Administrative endpoints for RAG management.
What it does: Implements ingestion and search routes secured by X-Admin-Key.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from api.dependencies import verify_admin_key
from models.schema import Product, TextEmbedding
from services.database import get_db
from services.rag import ingest_product_text, search_products

router = APIRouter(
    prefix="/admin/rag", tags=["admin"], dependencies=[Depends(verify_admin_key)]
)


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
async def admin_ingest(
    request: IngestRequest, db: Annotated[AsyncSession, Depends(get_db)]
):
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
async def admin_search(
    request: SearchRequest, db: Annotated[AsyncSession, Depends(get_db)]
):
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
