"""Why this exists: Verifies the end-to-end RAG pipeline and caching logic.
What it does: Tests product ingestion, vector search, and L1/L2 cache hits.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings
from models.schema import Product, SemanticCache, TextEmbedding
from services.rag import ingest_product_text, search_products
from services.semantic_cache import get_l1_cache, get_l2_cache, set_cache


@pytest_asyncio.fixture(scope="function")
async def db_session():
    """Provides a clean database session for each test using a fresh engine."""
    # Create a fresh engine for each test to avoid event loop issues
    test_engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
    )

    test_session_local = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with test_session_local() as session:
        yield session
        # Cleanup
        await session.execute(delete(TextEmbedding))
        await session.execute(delete(Product))
        await session.execute(delete(SemanticCache))
        await session.commit()

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_rag_ingestion_and_search(db_session: AsyncSession):
    """
    Why this exists: Verifies that products can be ingested and retrieved
    via vector search.
    """
    name = "Test Product"
    sku = f"test-sku-{uuid.uuid4()}"
    description = "This is a high-performance testing widget for integration tests."

    # 1. Ingest
    try:
        product_id = await ingest_product_text(
            db=db_session, name=name, sku=sku, description=description, price=99.99
        )
    except Exception as e:
        pytest.skip(f"Ingestion failed (Ollama may be offline): {e}")

    assert product_id is not None

    # 2. Verify DB state
    product = await db_session.get(Product, product_id)
    assert product.name == name
    assert product.sku == sku

    # 3. Search
    query = "performance widget"
    results = await search_products(db=db_session, query=query, top_k=1)

    assert len(results) > 0
    assert results[0]["sku"] == sku
    assert results[0]["score"] > 0.5


@pytest.mark.asyncio
async def test_semantic_cache_l1_l2(db_session: AsyncSession):
    """
    Why this exists: Verifies exact match (L1) and semantic match (L2) caching.
    """
    query = "What is the price of the testing widget?"
    response = "The testing widget costs $99.99."
    # Mock embedding (1024 dims)
    embedding = [0.1] * 1024
    model_name = "test-model"

    # 1. Initial Cache Miss
    l1_hit = await get_l1_cache(db_session, query)
    assert l1_hit is None

    # 2. Set Cache
    await set_cache(
        db=db_session,
        query=query,
        response=response,
        embedding=embedding,
        model_name=model_name,
    )

    # 3. L1 Hit (Exact Match)
    l1_hit = await get_l1_cache(db_session, query)
    assert l1_hit == response

    # 4. L2 Hit (Semantic Match)
    l2_hit = await get_l2_cache(db_session, embedding, threshold=0.99)
    assert l2_hit == response

    # 5. L2 Miss (Below Threshold)
    different_embedding = [-0.1] * 1024
    l2_miss = await get_l2_cache(db_session, different_embedding, threshold=0.99)
    assert l2_miss is None
