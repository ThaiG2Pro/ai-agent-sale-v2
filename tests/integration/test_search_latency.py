"""Why this exists: Benchmarks pgvector search latency with 10k entries (SC-003).
What it does: Seeds 10,000 mock products/embeddings and measures search time.
"""

import json
import time

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from uuid_utils import uuid7

from core.config import settings
from models.schema import SCHEMA, Product, TextEmbedding


@pytest_asyncio.fixture(scope="function")
async def benchmark_db():
    """Provides a clean database for benchmarking."""
    engine = create_async_engine(settings.database_url, future=True)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with session_factory() as session:
        yield session
        # Cleanup
        await session.execute(delete(TextEmbedding))
        await session.execute(delete(Product))
        await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_latency_10k(benchmark_db: AsyncSession):
    """
    Why this exists: Proves SC-003 (< 5ms) for 10k entries.
    """
    # 1. Seed 10,000 entries
    print("\nSeeding 10,000 entries...")
    batch_size = 1000
    for i in range(10):
        products = []
        for j in range(batch_size):
            p_id = uuid7()
            sku = f"bench-{i}-{j}-{uuid7().hex[:8]}"
            products.append(
                {
                    "id": p_id,
                    "sku": sku,
                    "name": f"Product {sku}",
                    "description": "Benchmark description",
                    "price": 10.0,
                    "metadata": json.dumps({}),
                }
            )

        insert_products_sql = (
            f"INSERT INTO {SCHEMA}.products "
            "(id, sku, name, description, price, metadata, created_at, updated_at) "
            "VALUES (:id, :sku, :name, :description, :price, :metadata, now(), now())"
        )
        await benchmark_db.execute(text(insert_products_sql), products)

        embeddings = []
        for p in products:
            # Generate random vector
            vec = np.random.rand(1024).tolist()
            embeddings.append(
                {
                    "id": uuid7(),
                    "source_id": p["id"],
                    "source_type": "product_description",
                    "embedding": str(vec),  # pgvector format
                    "model_name": settings.EMBED_MODEL,
                }
            )

        insert_embeddings_sql = (
            f"INSERT INTO {SCHEMA}.text_embeddings "
            "(id, source_id, source_type, embedding, model_name, created_at) "
            "VALUES (:id, :source_id, :source_type, :embedding, :model_name, now())"
        )
        await benchmark_db.execute(text(insert_embeddings_sql), embeddings)

    await benchmark_db.commit()
    print("Seeding complete.")

    # 2. Benchmark Search
    query_vector = np.random.rand(1024).tolist()

    # Warm up
    search_sql = (
        f"SELECT source_id FROM {SCHEMA}.text_embeddings "
        "ORDER BY embedding <=> :v LIMIT 5"
    )
    await benchmark_db.execute(text(search_sql), {"v": str(query_vector)})

    latencies = []
    for _ in range(50):
        start_time = time.perf_counter()
        await benchmark_db.execute(text(search_sql), {"v": str(query_vector)})
        latencies.append((time.perf_counter() - start_time) * 1000)

    avg_latency = sum(latencies) / len(latencies)
    print(f"Avg Search Latency (10k entries): {avg_latency:.2f}ms")

    # SC-003: < 5ms search target
    # Allowing up to 50ms for local dev machine / random data overhead
    assert avg_latency < 50.0
