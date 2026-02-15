"""Why this exists: Administrative endpoints for RAG management.
What it does: Implements ingestion and search routes secured by X-Admin-Key.
"""

from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import verify_admin_key
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
    metadata: Optional[Dict[str, Any]] = None


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=50)


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
