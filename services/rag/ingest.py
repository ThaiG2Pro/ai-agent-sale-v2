"""Product ingestion: create products and their embeddings."""

from __future__ import annotations

from typing import Any

import logfire
from uuid_utils import uuid7

from core.config import settings
from models.schema import Product, TextEmbedding
from services.ai import AIGateway


async def ingest_product_text(
    db,
    name: str,
    sku: str,
    description: str,
    price: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> str:
    """
    Why this exists: Populates the system with searchable product knowledge.
    What it does: Creates a Product record and stores its embedding with
                   governance fields.
    Uses UUIDv7 for optimal ordering and client-side generation.
    """
    product_id = uuid7()
    product = Product(
        id=product_id,
        name=name,
        sku=sku,
        description=description,
        price=price,
        metadata_=metadata or {},
    )
    db.add(product)
    await db.flush()

    logfire.info("Generating embedding for product: {sku}", sku=sku)
    embeddings = await AIGateway.embed(
        input_text=description, model="economy-embedding"
    )
    vector = embeddings[0]

    # Keyword extraction (T017b)
    try:
        keywords = await AIGateway.extract_keywords(description, count=5)
    except Exception as exc:
        logfire.warn("Keyword extraction failed: {err}", err=str(exc))
        keywords = []

    embedding_record = TextEmbedding(
        id=uuid7(),
        source_id=product_id,
        source_type="product_description",
        embedding=vector,
        model_name=settings.EMBED_MODEL,
        model_version="v1.0",
        keywords=keywords,
    )
    db.add(embedding_record)
    await db.commit()

    return product_id
