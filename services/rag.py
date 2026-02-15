"""Why this exists: Core business logic for RAG (Retrieval Augmented Generation).
What it does: Implements text ingestion into PostgreSQL and vector search capabilities.
"""

import uuid
from typing import Any, Dict, List, Optional

import litellm
import logfire
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from models.schema import Product, TextEmbedding


async def ingest_product_text(
    db: AsyncSession,
    name: str,
    sku: str,
    description: str,
    price: float = 0.0,
    metadata: Optional[Dict[str, Any]] = None,
) -> uuid.UUID:
    """
    Why this exists: Populates the system with searchable product knowledge.
    What it does: Creates a Product record and generates its embedding.
    """
    # 1. Create Product
    product = Product(
        name=name,
        sku=sku,
        description=description,
        price=price,
        metadata_=metadata or {},
    )
    db.add(product)
    await db.flush()  # Get product.id

    # 2. Generate Embedding via LiteLLM (Article XII: Zero-Cost Baseline with Ollama)
    # Using embed_model from settings
    logfire.info("Generating embedding for product: {sku}", sku=sku)
    response = await litellm.aembedding(
        model=settings.EMBED_MODEL,
        input=[description],
        api_base=settings.OLLAMA_BASE_URL if "ollama" in settings.EMBED_MODEL else None,
    )
    vector = response.data[0]["embedding"]

    # 3. Store Embedding
    embedding_record = TextEmbedding(
        source_id=product.id,
        source_type="product_description",
        embedding=vector,
        model_name=settings.EMBED_MODEL,
        model_version="v1.0",
    )
    db.add(embedding_record)
    await db.commit()

    return product.id


async def search_products(
    db: AsyncSession, query: str, top_k: int = 5
) -> List[Dict[str, Any]]:
    """
    Why this exists: Performs semantic retrieval for the AI agent.
    What it does: Embeds the query and searches pgvector for similar products.
    SC-003 Target: < 5ms search target (database search time).
    """
    # 1. Embed Query
    logfire.info("Searching products for query: {query}", query=query)
    response = await litellm.aembedding(
        model=settings.EMBED_MODEL,
        input=[query],
        api_base=settings.OLLAMA_BASE_URL if "ollama" in settings.EMBED_MODEL else None,
    )
    query_vector = response.data[0]["embedding"]

    # 2. Vector Search using pgvector
    # Join TextEmbedding with Product to return full data
    cos_dist = TextEmbedding.embedding.cosine_distance(query_vector)
    similarity = (1 - cos_dist).label("similarity")
    stmt = (
        select(Product, similarity)
        .join(TextEmbedding, Product.id == TextEmbedding.source_id)
        .order_by(cos_dist)
        .limit(top_k)
    )

    result = await db.execute(stmt)
    hits = []
    for product, sim_score in result.all():
        hits.append(
            {
                "id": str(product.id),
                "sku": product.sku,
                "name": product.name,
                "description": product.description,
                "score": float(sim_score),
                "metadata": product.metadata_,
            }
        )

    return hits
