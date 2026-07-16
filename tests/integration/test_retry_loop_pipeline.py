"""Why this exists: Integration coverage for the bounded RAG retry loop
(agentic-rag-retry-loop, ticket 2026) against a REAL Postgres test database (R-SDLC R10 —
never mock the DB in integration tests). Only the AIGateway boundary (embedding + the light-
tier rewrite/generation LLM calls) is mocked, since Ollama is not guaranteed to be running in
every environment — the same boundary `tests/integration/test_rag.py` already mocks/skips on.

What it does:
- Recover-on-rewrite: attempt 0 is insufficient, the rewrite + re-retrieval on attempt 1
  succeeds and `answer_with_rag` returns a generated answer with citations (AC-2026-009).
- Exhaustion: every attempt stays insufficient → decline, never a forced weak answer
  (AC-2026-016).
- COMPARISON mutual exclusion: `retrieve_with_retry` never loops for COMPARISON intent, even
  when declined and budget remains (AC-2026-020).
- Cache isolation: only the FINAL accepted query/answer is written to `semantic_cache` —
  intermediate rewrites are never cached (AC-2026-023).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from uuid_utils import uuid7

from core.config import settings
from models.schema import Product, SemanticCache, TextEmbedding
from services.ai import RewrittenQuery
from services.rag.constants import DECLINE_MESSAGE
from services.rag.pipeline import answer_with_rag, retrieve_with_retry

# A unit vector and an orthogonal unit vector (cosine similarity 0) in EMBED_DIMENSION space —
# lets us deterministically control vector_score without any real embedding model.
_DIM = settings.EMBED_DIMENSION
_NEAR_VECTOR = [1.0] + [0.0] * (_DIM - 1)  # identical to the stored embedding → cos_sim = 1.0
_FAR_VECTOR = [0.0] * (_DIM - 1) + [1.0]  # orthogonal to the stored embedding → cos_sim = 0.0


async def _insert_product(db_session, *, name: str, description: str, sku: str) -> None:
    product = Product(
        sku=sku,
        name=name,
        description=description,
        price=Decimal("299.99"),
        stock_quantity=10,
    )
    db_session.add(product)
    await db_session.flush()

    embedding = TextEmbedding(
        source_id=product.id,
        source_type="product_description",
        embedding=_NEAR_VECTOR,
        model_name=settings.EMBED_MODEL,
        model_version="v1.0",
        keywords=[],
    )
    db_session.add(embedding)
    await db_session.commit()


@pytest.mark.asyncio
async def test_recover_on_rewrite_produces_answer(db_session):
    """AC-2026-009: attempt 0 insufficient (far/unrelated embedding), the rewrite + attempt 1
    (embedding lands right on the stored vector) recovers and an answer is generated."""
    await _insert_product(
        db_session,
        name="Máy pha cà phê Espresso Deluxe",
        description="Máy pha cà phê Espresso Deluxe với áp suất 15 bar, bình chứa 1.5 lít.",
        sku=f"coffee-{uuid7()}",
    )

    llm_answer = AsyncMock()
    llm_answer.choices = [
        AsyncMock(message=AsyncMock(content="Máy pha cà phê Espresso Deluxe có áp suất 15 bar."))
    ]

    with (
        patch(
            "services.rag.pipeline.AIGateway.embed",
            new_callable=AsyncMock,
            side_effect=[[_FAR_VECTOR], [_NEAR_VECTOR]],
        ) as mock_embed,
        patch(
            "services.ai.AIGateway.rewrite_query",
            new_callable=AsyncMock,
            return_value=RewrittenQuery(
                query="máy pha cà phê Espresso Deluxe áp suất bao nhiêu bar",
                keeps_subject=True,
            ),
        ) as mock_rewrite,
        patch(
            "services.rag.pipeline.AIGateway.complete",
            new_callable=AsyncMock,
            return_value=llm_answer,
        ),
    ):
        result = await answer_with_rag(
            db_session, query="thời tiết ngày mai có mưa không", model="economy-chat"
        )

    assert mock_embed.call_count == 2  # attempt 0 + attempt 1 (post-rewrite)
    mock_rewrite.assert_called_once()
    assert result.declined is False
    assert len(result.citations) > 0
    assert result.answer == "Máy pha cà phê Espresso Deluxe có áp suất 15 bar."


@pytest.mark.asyncio
async def test_exhaustion_still_insufficient_declines(db_session, monkeypatch):
    """AC-2026-016: budget exhausted while still insufficient → current decline behavior,
    never a forced best-effort answer."""
    monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 1)
    await _insert_product(
        db_session,
        name="Tủ lạnh Inverter 300 lít",
        description="Tủ lạnh Inverter 300 lít tiết kiệm điện, làm đá tự động.",
        sku=f"fridge-{uuid7()}",
    )

    with (
        patch(
            "services.rag.pipeline.AIGateway.embed",
            new_callable=AsyncMock,
            side_effect=[[_FAR_VECTOR], [_FAR_VECTOR]],  # never lands on the stored vector
        ),
        patch(
            "services.ai.AIGateway.rewrite_query",
            new_callable=AsyncMock,
            return_value=RewrittenQuery(query="một câu hỏi khác về tủ lạnh", keeps_subject=True),
        ),
    ):
        result = await answer_with_rag(db_session, query="dự báo thời tiết tuần này")

    assert result.declined is True
    assert result.answer == DECLINE_MESSAGE


@pytest.mark.asyncio
async def test_comparison_intent_no_double_retrieval_storm(db_session, monkeypatch):
    """AC-2026-020, INT-2026-006: COMPARISON never enters the retry loop, even when declined
    and budget remains — the split fallback in retrieval_node (untouched by this change)
    handles COMPARISON recovery instead."""
    monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 2)
    # empty catalog — guarantees a Layer-1 decline (chunks_after == 0) on the single pass
    with (
        patch(
            "services.rag.pipeline.AIGateway.embed",
            new_callable=AsyncMock,
            return_value=[_FAR_VECTOR],
        ) as mock_embed,
        patch("services.ai.AIGateway.rewrite_query", new_callable=AsyncMock) as mock_rewrite,
    ):
        result = await retrieve_with_retry(
            db_session, "Sản phẩm A và sản phẩm B cái nào tốt hơn", intent="COMPARISON"
        )

    assert result.declined is True
    assert mock_embed.call_count == 1  # single retrieval pass — no retry storm
    mock_rewrite.assert_not_called()


@pytest.mark.asyncio
async def test_answer_with_rag_caches_only_final_accepted_query(db_session):
    """AC-2026-023: only the FINAL accepted (rewritten) query/answer is cached — the original,
    insufficient attempt-0 query is never written to semantic_cache."""
    await _insert_product(
        db_session,
        name="Loa Bluetooth Mini SoundBox",
        description="Loa Bluetooth Mini SoundBox chống nước IPX7, pin 12 giờ.",
        sku=f"speaker-{uuid7()}",
    )
    rewritten_query = "loa Bluetooth Mini SoundBox pin bao nhiêu giờ"

    llm_answer = AsyncMock()
    llm_answer.choices = [
        AsyncMock(message=AsyncMock(content="Loa Bluetooth Mini SoundBox có pin 12 giờ."))
    ]

    with (
        patch(
            "services.rag.pipeline.AIGateway.embed",
            new_callable=AsyncMock,
            side_effect=[[_FAR_VECTOR], [_NEAR_VECTOR]],
        ),
        patch(
            "services.ai.AIGateway.rewrite_query",
            new_callable=AsyncMock,
            return_value=RewrittenQuery(query=rewritten_query, keeps_subject=True),
        ),
        patch(
            "services.rag.pipeline.AIGateway.complete",
            new_callable=AsyncMock,
            return_value=llm_answer,
        ),
    ):
        result = await answer_with_rag(db_session, query="mưa có to không ngày mai")

    assert result.declined is False

    rows = (await db_session.execute(select(SemanticCache))).scalars().all()
    assert len(rows) == 1  # exactly one cache row — no intermediate-attempt cache pollution
    assert rows[0].query_text == rewritten_query  # the FINAL accepted query, not the original
