"""Unit tests for services/rag/retrieval.py — RRF merge + degradation paths.

Covers: rank fusion across vector+FTS (overlap and FTS-only hits), vector
timeout hard-fail, FTS failure falling back to vector-only (with rollback),
and the search_products entry point including its error shields.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.rag.retrieval import hybrid_search_rrf, search_products


def _vec_row(cid: str, name: str, score: float = 0.9) -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id=cid,
        product_id=f"prod-{cid}",
        sku=f"SKU-{cid}",
        name=name,
        description="desc",
        price=100.0,
        metadata={"cat": "toys"},
        vector_score=score,
    )


def _fts_row(cid: str, name: str, score: float = 0.5) -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id=cid,
        product_id=f"prod-{cid}",
        sku=f"SKU-{cid}",
        name=name,
        description="desc",
        price=100.0,
        metadata=None,
        fts_score=score,
    )


def _db_with(vector_rows, fts_result):
    """db.execute → first call returns vector rows, second the FTS result."""
    db = AsyncMock()
    vec_res = MagicMock()
    vec_res.all.return_value = vector_rows
    if isinstance(fts_result, Exception):
        db.execute.side_effect = [vec_res, fts_result]
    else:
        fts_res = MagicMock()
        fts_res.all.return_value = fts_result
        db.execute.side_effect = [vec_res, fts_res]
    return db


@pytest.mark.asyncio
async def test_rrf_merges_overlapping_hit_to_top():
    """A chunk found by BOTH sources outranks single-source chunks."""
    db = _db_with(
        vector_rows=[_vec_row("a", "A"), _vec_row("b", "B")],
        fts_result=[_fts_row("b", "B"), _fts_row("c", "C")],
    )

    merged = await hybrid_search_rrf(db, [0.1] * 4, "query", top_k=3)

    assert merged[0]["chunk_id"] == "b"  # double signal wins
    b = merged[0]
    assert b["vector_score"] > 0 and b["fts_score"] > 0
    # FTS-only hit present with vector_score 0.0
    c = next(x for x in merged if x["chunk_id"] == "c")
    assert c["vector_score"] == 0.0


@pytest.mark.asyncio
async def test_rrf_respects_top_k():
    db = _db_with(
        vector_rows=[_vec_row(str(i), f"P{i}") for i in range(6)],
        fts_result=[],
    )
    merged = await hybrid_search_rrf(db, [0.1] * 4, "q", top_k=2)
    assert len(merged) == 2


@pytest.mark.asyncio
async def test_vector_timeout_returns_empty():
    db = AsyncMock()
    with patch(
        "services.rag.retrieval.asyncio.wait_for",
        AsyncMock(side_effect=TimeoutError),
    ):
        assert await hybrid_search_rrf(db, [0.1] * 4, "q", top_k=3) == []


@pytest.mark.asyncio
async def test_fts_failure_falls_back_to_vector_only_with_rollback():
    """FTS SQL error must not kill the search — vector results still returned."""
    db = _db_with(
        vector_rows=[_vec_row("a", "A")],
        fts_result=RuntimeError("relation does not exist"),
    )

    merged = await hybrid_search_rrf(db, [0.1] * 4, "q", top_k=3)

    assert [c["chunk_id"] for c in merged] == ["a"]
    db.rollback.assert_awaited_once()  # failed tx cleaned up


@pytest.mark.asyncio
async def test_search_products_adds_score_alias():
    db = _db_with(vector_rows=[_vec_row("a", "A")], fts_result=[])
    with patch("services.ai.AIGateway.embed", AsyncMock(return_value=[[0.1] * 4])):
        results = await search_products(db, "đồ chơi", top_k=3)

    assert results and results[0]["score"] == results[0]["rrf_score"]


@pytest.mark.asyncio
async def test_search_products_swallows_embed_failure():
    db = AsyncMock()
    with patch(
        "services.ai.AIGateway.embed",
        AsyncMock(side_effect=RuntimeError("Ollama down")),
    ):
        assert await search_products(db, "q") == []


@pytest.mark.asyncio
async def test_search_products_timeout_returns_empty():
    db = AsyncMock()
    with patch("services.ai.AIGateway.embed", AsyncMock(return_value=[[0.1] * 4])):
        with patch(
            "services.rag.retrieval.asyncio.wait_for",
            AsyncMock(side_effect=asyncio.TimeoutError),
        ):
            assert await search_products(db, "q") == []
