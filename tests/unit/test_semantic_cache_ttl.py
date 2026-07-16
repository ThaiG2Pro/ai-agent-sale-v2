"""Tests for semantic cache TTL expiry + invalidation (WP5).

Demo-critical: after a price change, the cache must stop serving the old
answer once the TTL window passes, and ingest must flush it immediately.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from models.schema import SemanticCache
from services.semantic_cache import (
    generate_query_hash,
    get_l1_cache,
    get_l2_cache,
    invalidate_cache,
    set_cache,
)

QUERY = "giá samsung s24 ultra?"
EMBEDDING = [1.0] + [0.0] * (settings.EMBED_DIMENSION - 1)


async def _insert_entry(db, *, age_seconds: int, response: str = "old price 100k") -> None:
    db.add(
        SemanticCache(
            query_hash=generate_query_hash(QUERY),
            query_text=QUERY,
            response=response,
            embedding=EMBEDDING,
            model_name=settings.EMBED_MODEL,
            citations=[],
            created_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_l1_serves_fresh_entry(db_session) -> None:
    await _insert_entry(db_session, age_seconds=0, response="fresh answer")
    hit = await get_l1_cache(db_session, QUERY)
    assert hit is not None
    assert hit["response"] == "fresh answer"


@pytest.mark.asyncio
async def test_l1_ignores_expired_entry(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "CACHE_TTL_SECONDS", 60)
    await _insert_entry(db_session, age_seconds=120)
    assert await get_l1_cache(db_session, QUERY) is None


@pytest.mark.asyncio
async def test_l2_ignores_expired_entry(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "CACHE_TTL_SECONDS", 60)
    await _insert_entry(db_session, age_seconds=120)
    assert await get_l2_cache(db_session, EMBEDDING, threshold=0.9) is None


@pytest.mark.asyncio
async def test_l2_serves_fresh_entry(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "CACHE_TTL_SECONDS", 3600)
    await _insert_entry(db_session, age_seconds=0)
    hit = await get_l2_cache(db_session, EMBEDDING, threshold=0.9)
    assert hit is not None


@pytest.mark.asyncio
async def test_ttl_zero_disables_expiry(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "CACHE_TTL_SECONDS", 0)
    await _insert_entry(db_session, age_seconds=10_000_000)
    assert await get_l1_cache(db_session, QUERY) is not None


@pytest.mark.asyncio
async def test_set_cache_refreshes_ttl_window(db_session, monkeypatch) -> None:
    """Re-caching the same query must reset created_at (merge-update path)."""
    monkeypatch.setattr(settings, "CACHE_TTL_SECONDS", 60)
    await _insert_entry(db_session, age_seconds=120, response="stale")
    assert await get_l1_cache(db_session, QUERY) is None

    await set_cache(
        db_session,
        query=QUERY,
        response="new price 90k",
        embedding=EMBEDDING,
        model_name=settings.EMBED_MODEL,
    )
    hit = await get_l1_cache(db_session, QUERY)
    assert hit is not None
    assert hit["response"] == "new price 90k"


@pytest.mark.asyncio
async def test_invalidate_cache_removes_all_entries(db_session) -> None:
    await _insert_entry(db_session, age_seconds=0)
    removed = await invalidate_cache(db_session)
    assert removed >= 1
    assert await get_l1_cache(db_session, QUERY) is None
