"""Integration tests for hybrid_search_rrf (Phase 4).

These tests are best-effort and will skip if external services (Ollama) are
unavailable in the test environment, matching the pattern used elsewhere.
"""

import pytest
from uuid_utils import uuid7

from services.ai import AIGateway
from services.rag import hybrid_search_rrf, ingest_product_text


@pytest.mark.asyncio
async def test_hybrid_search_rrf_returns_ranked_results(db_session):
    """Ingest three products and verify hybrid_search_rrf returns ranked results."""
    try:
        await ingest_product_text(
            db=db_session,
            name="Hybrid A",
            sku=f"hyb-a-{uuid7()}",
            description="Widget Bluetooth with sensors and connectivity",
            price=10.0,
        )
        await ingest_product_text(
            db=db_session,
            name="Hybrid B",
            sku=f"hyb-b-{uuid7()}",
            description="Battery powered widget for testing",
            price=11.0,
        )
        await ingest_product_text(
            db=db_session,
            name="Hybrid C",
            sku=f"hyb-c-{uuid7()}",
            description="Affordable widget for general use",
            price=5.0,
        )
    except Exception as e:
        pytest.skip(f"Ingestion failed or embedding service unavailable: {e}")

    try:
        embeddings = await AIGateway.embed(
            input_text="widget bluetooth", model="economy-embedding"
        )
        qvec = embeddings[0]
    except Exception as e:
        pytest.skip(f"Embedding service unavailable: {e}")

    results = await hybrid_search_rrf(db_session, qvec, "widget bluetooth", top_k=3)

    assert isinstance(results, list)
    assert len(results) > 0
    assert all(
        all(k in r for k in ("chunk_id", "id", "rrf_score", "vector_score")) for r in results
    )

    rrf_scores = [r["rrf_score"] for r in results]
    assert rrf_scores == sorted(rrf_scores, reverse=True)
    assert all(r > 0 for r in rrf_scores)


@pytest.mark.asyncio
async def test_hybrid_surfaces_fts_keyword_match(db_session):
    """Validate that an FTS-only hit can be surfaced by RRF when vector ranks low."""
    sku = f"fts-{uuid7()}"
    try:
        await ingest_product_text(
            db=db_session,
            name="Bluetooth Product",
            sku=sku,
            description="Sản phẩm có kết nối Bluetooth và âm thanh chất lượng",
            price=20.0,
        )
    except Exception as e:
        pytest.skip(f"Ingestion failed or embedding service unavailable: {e}")

    try:
        # Use an unrelated query vector so vector-only search ranks it low
        embeddings = await AIGateway.embed(
            input_text="completely unrelated query xyz",
            model="economy-embedding",
        )
        qvec = embeddings[0]
    except Exception as e:
        pytest.skip(f"Embedding service unavailable: {e}")

    results = await hybrid_search_rrf(db_session, qvec, "Bluetooth", top_k=3)
    skus = [r.get("sku") for r in results]
    assert sku in skus
