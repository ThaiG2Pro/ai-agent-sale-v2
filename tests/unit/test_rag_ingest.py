"""Unit tests for services/rag/ingest.py — enrichment pipeline, LLM mocked.

Covers: structured keyword extraction (success + graceful [] fallback),
metadata enrichment (success + minimal fallback), the hallucination-critic
validator (pure logic), and ingest_product_text's idempotent-skip and
valid/invalid-metadata branches.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai import ProductMetadata
from services.rag.ingest import (
    enrich_metadata_async,
    extract_keywords_structured,
    ingest_product_text,
    validate_metadata_vs_source,
)


def _llm_json_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.choices[0].message.content = json.dumps(payload)
    return resp


def _metadata(**overrides) -> ProductMetadata:
    base = {
        "product_id": "SKU-1",
        "technical_specs": {"pin": "5000mAh", "màn hình": "6.7 inch"},
        "keywords": ["điện thoại", "pin trâu", "5000mAh"],
        "seo_summary": "Điện thoại pin trâu",
        "category": "Electronics",
        "intent": "consumer",
    }
    base.update(overrides)
    return ProductMetadata(**base)


# --- extract_keywords_structured -------------------------------------------


@pytest.mark.asyncio
async def test_extract_keywords_returns_capped_list():
    resp = _llm_json_response(
        {"keywords": ["a", "b", "c", "d", "e", "f", "g"], "rationale": "search terms"}
    )
    with patch("services.ai.ai_router.acompletion", AsyncMock(return_value=resp)):
        keywords = await extract_keywords_structured("desc", "Phone X", count=5)
    assert keywords == ["a", "b", "c", "d", "e"]  # capped at count


@pytest.mark.asyncio
async def test_extract_keywords_failure_returns_empty_list():
    """Offline-First: LLM down must not break ingestion — [] fallback."""
    with patch(
        "services.ai.ai_router.acompletion",
        AsyncMock(side_effect=ConnectionError("Ollama down")),
    ):
        assert await extract_keywords_structured("desc", "Phone X") == []


# --- enrich_metadata_async ---------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_metadata_parses_llm_output():
    resp = _llm_json_response(_metadata().model_dump())
    with patch("services.ai.ai_router.acompletion", AsyncMock(return_value=resp)):
        enriched = await enrich_metadata_async("desc", "Phone X", "SKU-1")
    assert enriched.technical_specs["pin"] == "5000mAh"
    assert enriched.category == "Electronics"


@pytest.mark.asyncio
async def test_enrich_metadata_falls_back_to_minimal_on_failure():
    with patch(
        "services.ai.ai_router.acompletion",
        AsyncMock(side_effect=RuntimeError("model offline")),
    ):
        enriched = await enrich_metadata_async("desc", "Phone X", "SKU-1")
    assert enriched == ProductMetadata.minimal("SKU-1", "Phone X")


# --- validate_metadata_vs_source (pure critic logic) ------------------------


@pytest.mark.asyncio
async def test_validation_passes_when_spec_values_found_in_text():
    text = "Điện thoại pin 5000mAh, màn hình 6.7 inch sắc nét"
    assert await validate_metadata_vs_source(text, _metadata()) is True


@pytest.mark.asyncio
async def test_validation_fails_on_hallucinated_specs():
    """No spec value appears in the source text → likely hallucinated."""
    text = "Áo thun cotton thoáng mát"
    assert await validate_metadata_vs_source(text, _metadata()) is False


@pytest.mark.asyncio
async def test_validation_passes_with_no_specs():
    """Simple products without specs are OK — nothing to hallucinate."""
    meta = _metadata(technical_specs={})
    assert await validate_metadata_vs_source("Áo thun", meta) is True


# --- ingest_product_text -----------------------------------------------------


def _db(existing_product=None) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing_product
    db.execute.return_value = result
    db.add = MagicMock()
    return db


@pytest.mark.asyncio
async def test_ingest_skips_existing_sku():
    existing = MagicMock()
    existing.id = "existing-id"
    db = _db(existing_product=existing)

    result = await ingest_product_text(db, name="P", sku="SKU-1", description="d")

    assert result == "existing-id"
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_stores_enriched_metadata_when_valid():
    db = _db()
    meta = _metadata()
    with (
        patch("services.rag.ingest.AIGateway.embed", AsyncMock(return_value=[[0.1] * 4])),
        patch("services.rag.ingest.enrich_metadata_async", AsyncMock(return_value=meta)),
        patch("services.rag.ingest.validate_metadata_vs_source", AsyncMock(return_value=True)),
        patch(
            "services.rag.ingest.extract_keywords_structured",
            AsyncMock(return_value=["pin trâu"]),
        ),
        patch("services.rag.ingest.invalidate_cache", AsyncMock()) as inv,
    ):
        product_id = await ingest_product_text(
            db, name="Phone X", sku="SKU-1", description="d", price=100.0
        )

    assert product_id
    product = db.add.call_args_list[0].args[0]
    assert product.metadata_ == meta.model_dump()
    embedding = db.add.call_args_list[1].args[0]
    assert embedding.keywords == ["pin trâu"]
    db.commit.assert_awaited_once()
    inv.assert_awaited_once()  # catalog changed → semantic cache invalidated


@pytest.mark.asyncio
async def test_ingest_falls_back_to_minimal_metadata_when_invalid():
    """Critic rejects hallucinated metadata → minimal metadata + enriched
    keywords are NOT trusted either."""
    db = _db()
    with (
        patch("services.rag.ingest.AIGateway.embed", AsyncMock(return_value=[[0.1] * 4])),
        patch(
            "services.rag.ingest.enrich_metadata_async",
            AsyncMock(return_value=_metadata()),
        ),
        patch(
            "services.rag.ingest.validate_metadata_vs_source",
            AsyncMock(return_value=False),
        ),
        patch("services.rag.ingest.extract_keywords_structured", AsyncMock(return_value=[])),
        patch("services.rag.ingest.invalidate_cache", AsyncMock()),
    ):
        await ingest_product_text(db, name="Phone X", sku="SKU-1", description="d")

    product = db.add.call_args_list[0].args[0]
    assert product.metadata_ == ProductMetadata.minimal("SKU-1", "Phone X").model_dump()
    embedding = db.add.call_args_list[1].args[0]
    assert embedding.keywords == []
