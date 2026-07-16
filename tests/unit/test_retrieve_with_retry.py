"""Why this exists: TDD for the bounded RAG retry loop (agentic-rag-retry-loop, ticket 2026).

What it does: Exercises `retrieve_with_retry` (services/rag/pipeline.py) — the sufficiency
gate that reuses existing `RetrievalResult` signals with no new scorer (ADR-002), the
bounded for-loop cap/kill-switch (ADR-003), COMPARISON mutual exclusion (ADR-004), and
per-attempt observability (AC-2026-021). Also covers the D2 kill-switch decline-text parity
(RISK-005) via `answer_with_rag`.

All external calls (`search_and_retrieve`, `AIGateway.rewrite_query`, DB writes) are mocked —
no network, no Ollama, no dependency on a real product catalog. Article III 3.1: deterministic
control-flow logic follows strict TDD.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config import settings
from services.ai import RewrittenQuery
from services.rag.pipeline import (
    RetrievalResult,
    _write_retry_trace,
    answer_with_rag,
    retrieve_with_retry,
)


def _result(
    *,
    declined: bool,
    query_vector: list[float] | None = None,
    best_similarity: float = 0.0,
    citations: list[dict] | None = None,
    canonical_query: str = "original query",
    cached_answer: str | None = None,
    decline_reason: str | None = None,
    query_category: str = "short",
    top_k_used: int = 5,
) -> RetrievalResult:
    """Builds a minimal RetrievalResult for a given attempt outcome."""
    return RetrievalResult(
        cached_answer=cached_answer,
        cached_citations=[],
        declined=declined,
        citations=citations or [],
        chunks=[],
        best_similarity=best_similarity,
        canonical_query=canonical_query,
        query_vector=[0.1, 0.2] if query_vector is None else query_vector,
        query_category=query_category,
        top_k_used=top_k_used,
        decline_reason=decline_reason,
    )


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _rw(query: str, keeps_subject: bool = True) -> RewrittenQuery:
    return RewrittenQuery(query=query, keeps_subject=keeps_subject)


# ═══════════════════════════════════════════════════════════════════════════
# Gate classification — ADR-002 (reuse existing signals, no new scorer)
# ═══════════════════════════════════════════════════════════════════════════


class TestGateClassification:
    @pytest.mark.asyncio
    async def test_sufficient_first_pass_accepted_no_loop(self, monkeypatch):
        """AC-2026-001: sufficient result accepted, loop never entered."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 1)
        first = _result(declined=False, best_similarity=0.8, citations=[{"chunk_id": "c1"}])
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve", new_callable=AsyncMock
            ) as mock_search,
            patch("services.ai.AIGateway.rewrite_query", new_callable=AsyncMock) as mock_rw,
        ):
            mock_search.return_value = first
            result = await retrieve_with_retry(_mock_db(), "query", intent="INFO_QUERY")

        assert result is first
        mock_search.assert_called_once()
        mock_rw.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_hit_skips_loop(self, monkeypatch):
        """AC-2026-019: L1/L2 cache hit (declined=False, cached_answer set) never loops."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 1)
        cached = _result(declined=False, cached_answer="cached answer", best_similarity=1.0)
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve", new_callable=AsyncMock
            ) as mock_search,
            patch("services.ai.AIGateway.rewrite_query", new_callable=AsyncMock) as mock_rw,
        ):
            mock_search.return_value = cached
            result = await retrieve_with_retry(_mock_db(), "query")

        assert result.cached_answer == "cached answer"
        mock_rw.assert_not_called()

    @pytest.mark.asyncio
    async def test_spam_bypasses_loop(self, monkeypatch):
        """AC-2026-022: spam (is_valid=false) declined with empty query_vector bypasses loop."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 1)
        spam = _result(declined=True, query_vector=[], decline_reason="spam")
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve", new_callable=AsyncMock
            ) as mock_search,
            patch("services.ai.AIGateway.rewrite_query", new_callable=AsyncMock) as mock_rw,
        ):
            mock_search.return_value = spam
            result = await retrieve_with_retry(_mock_db(), "asdkjh qwer")

        assert result.decline_reason == "spam"
        mock_rw.assert_not_called()
        mock_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_unavailable_bypasses_loop(self, monkeypatch):
        """AC-2026-011 (bypass leg): embed-down never retries a dead embed service."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 1)
        embed_down = _result(declined=True, query_vector=[], decline_reason="embed_unavailable")
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve", new_callable=AsyncMock
            ) as mock_search,
            patch("services.ai.AIGateway.rewrite_query", new_callable=AsyncMock) as mock_rw,
        ):
            mock_search.return_value = embed_down
            result = await retrieve_with_retry(_mock_db(), "query")

        assert result.decline_reason == "embed_unavailable"
        mock_rw.assert_not_called()

    @pytest.mark.asyncio
    async def test_layer1_insufficient_with_budget_enters_loop(self, monkeypatch):
        """AC-2026-003, AC-2026-005: Layer-1 decline w/ populated vector + budget → rewrite."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 1)
        first = _result(
            declined=True,
            query_vector=[0.1, 0.2],
            best_similarity=0.2,
            decline_reason="layer1_guard",
        )
        second = _result(declined=True, query_vector=[0.1, 0.2], best_similarity=0.1)
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                side_effect=[first, second],
            ) as mock_search,
            patch(
                "services.ai.AIGateway.rewrite_query",
                new_callable=AsyncMock,
                return_value=_rw("better query"),
            ) as mock_rw,
        ):
            result = await retrieve_with_retry(_mock_db(), "vague query", intent="INFO_QUERY")

        mock_rw.assert_called_once()
        assert mock_search.call_count == 2
        # second attempt did not improve → best-seen (attempt 0) returned
        assert result is first

    @pytest.mark.asyncio
    async def test_missing_confidence_signals_no_crash(self, monkeypatch):
        """AC-2026-006: best_similarity defaults to 0.0 (no vector scores) → treated as
        insufficient, retry attempted, no exception propagates."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 1)
        zero_sim = _result(
            declined=True, query_vector=[0.1], best_similarity=0.0, decline_reason="layer1_guard"
        )
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                return_value=zero_sim,
            ),
            patch(
                "services.ai.AIGateway.rewrite_query",
                new_callable=AsyncMock,
                side_effect=Exception("ollama down"),
            ),
        ):
            result = await retrieve_with_retry(_mock_db(), "query")

        assert result is zero_sim  # no crash, best-seen returned

    @pytest.mark.asyncio
    async def test_declined_false_takes_priority_over_empty_vector(self, monkeypatch):
        """AC-2026-002: classification order is declined-first, then query_vector presence —
        reuses ONLY existing RetrievalResult fields, no new numeric scorer."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 1)
        # declined=False with an (unusual) empty vector still must be accepted immediately —
        # proves the gate never inspects a THIRD signal ahead of `declined`.
        odd = _result(declined=False, query_vector=[], best_similarity=0.9)
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                return_value=odd,
            ),
            patch("services.ai.AIGateway.rewrite_query", new_callable=AsyncMock) as mock_rw,
        ):
            result = await retrieve_with_retry(_mock_db(), "query")

        assert result is odd
        mock_rw.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# Cap / kill-switch / no-progress termination — ADR-003
# ═══════════════════════════════════════════════════════════════════════════


class TestCapAndKillSwitch:
    @pytest.mark.asyncio
    async def test_kill_switch_zero_returns_first_pass_untouched(self, monkeypatch):
        """AC-2026-014: RAG_RETRY_MAX_ATTEMPTS=0 → byte-identical to static single pass."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 0)
        first = _result(
            declined=True,
            query_vector=[0.1],
            best_similarity=0.1,
            decline_reason="layer1_guard",
        )
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                return_value=first,
            ) as mock_search,
            patch("services.ai.AIGateway.rewrite_query", new_callable=AsyncMock) as mock_rw,
        ):
            result = await retrieve_with_retry(_mock_db(), "query")

        assert result is first
        mock_search.assert_called_once()
        mock_rw.assert_not_called()

    @pytest.mark.asyncio
    async def test_cap_one_never_exceeded_on_no_progress(self, monkeypatch):
        """AC-2026-013, AC-2026-017: max=1 → at most 1 rewrite + 1 re-search, even though the
        loop range would otherwise allow more if the cap were higher."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 1)
        attempt0 = _result(
            declined=True, query_vector=[0.1], best_similarity=0.2, decline_reason="layer1_guard"
        )
        attempt1 = _result(declined=True, query_vector=[0.1], best_similarity=0.1)  # no gain
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                side_effect=[attempt0, attempt1],
            ) as mock_search,
            patch(
                "services.ai.AIGateway.rewrite_query",
                new_callable=AsyncMock,
                return_value=_rw("rewritten once"),
            ) as mock_rw,
        ):
            await retrieve_with_retry(_mock_db(), "query", intent="INFO_QUERY")

        assert mock_search.call_count == 2  # attempt 0 + attempt 1, never a 2nd rewrite round
        assert mock_rw.call_count == 1

    @pytest.mark.asyncio
    async def test_cap_two_never_exceeded(self, monkeypatch):
        """AC-2026-013, AC-2026-017: max=2 → at most 2 rewrite rounds even when each attempt
        makes progress and never succeeds."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 2)
        attempt0 = _result(
            declined=True,
            query_vector=[0.1],
            best_similarity=0.1,
            citations=[{"chunk_id": "a"}],
            decline_reason="layer1_guard",
        )
        attempt1 = _result(
            declined=True, query_vector=[0.1], best_similarity=0.2, citations=[{"chunk_id": "b"}]
        )
        attempt2 = _result(
            declined=True, query_vector=[0.1], best_similarity=0.3, citations=[{"chunk_id": "c"}]
        )
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                side_effect=[attempt0, attempt1, attempt2],
            ) as mock_search,
            patch(
                "services.ai.AIGateway.rewrite_query",
                new_callable=AsyncMock,
                side_effect=[_rw("rewrite 1"), _rw("rewrite 2")],
            ) as mock_rw,
        ):
            result = await retrieve_with_retry(_mock_db(), "query", intent="INFO_QUERY")

        assert mock_search.call_count == 3  # attempt 0, 1, 2 — never a 3rd rewrite round
        assert mock_rw.call_count == 2
        assert result.best_similarity == 0.3  # best-seen after exhaustion (AC-2026-016)

    @pytest.mark.asyncio
    async def test_no_progress_empty_rewrite_stops(self, monkeypatch):
        """AC-2026-012: an empty rewrite is no-progress → loop stops, no re-search."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 2)
        attempt0 = _result(
            declined=True, query_vector=[0.1], best_similarity=0.1, decline_reason="layer1_guard"
        )
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                return_value=attempt0,
            ) as mock_search,
            patch(
                "services.ai.AIGateway.rewrite_query",
                new_callable=AsyncMock,
                return_value=_rw("   "),
            ),
        ):
            result = await retrieve_with_retry(_mock_db(), "query")

        assert result is attempt0
        mock_search.assert_called_once()  # no re-search after empty rewrite

    @pytest.mark.asyncio
    async def test_no_progress_identical_rewrite_stops(self, monkeypatch):
        """AC-2026-012: a rewrite identical (case-insensitive) to the current query is
        no-progress."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 2)
        attempt0 = _result(
            declined=True,
            query_vector=[0.1],
            best_similarity=0.1,
            canonical_query="Same Query",
            decline_reason="layer1_guard",
        )
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                return_value=attempt0,
            ) as mock_search,
            patch(
                "services.ai.AIGateway.rewrite_query",
                new_callable=AsyncMock,
                return_value=_rw("same query"),  # identical, different case
            ),
        ):
            result = await retrieve_with_retry(_mock_db(), "Same Query")

        assert result is attempt0
        mock_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_subject_drift_discarded(self, monkeypatch):
        """AC-2026-012 (BR-2026-004, G2): keeps_subject=False → rewrite discarded, no re-search.
        Also covers a malformed-rewrite fallback (AC-2026-004), which surfaces identically as
        keeps_subject=False from AIGateway.rewrite_query's own graceful fallback."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 2)
        attempt0 = _result(
            declined=True, query_vector=[0.1], best_similarity=0.1, decline_reason="layer1_guard"
        )
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                return_value=attempt0,
            ) as mock_search,
            patch(
                "services.ai.AIGateway.rewrite_query",
                new_callable=AsyncMock,
                return_value=_rw("a totally different product", keeps_subject=False),
            ),
        ):
            result = await retrieve_with_retry(_mock_db(), "query")

        assert result is attempt0
        mock_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_similarity_gain_stops(self, monkeypatch):
        """AC-2026-015: re-retrieval with no similarity improvement stops the loop even with
        budget remaining."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 2)
        attempt0 = _result(
            declined=True,
            query_vector=[0.1],
            best_similarity=0.3,
            citations=[{"chunk_id": "x"}],
            decline_reason="layer1_guard",
        )
        attempt1 = _result(
            declined=True, query_vector=[0.1], best_similarity=0.3, citations=[{"chunk_id": "y"}]
        )  # different chunks, but similarity did NOT improve
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                side_effect=[attempt0, attempt1],
            ) as mock_search,
            patch(
                "services.ai.AIGateway.rewrite_query",
                new_callable=AsyncMock,
                return_value=_rw("rewritten"),
            ) as mock_rw,
        ):
            result = await retrieve_with_retry(_mock_db(), "query", intent="INFO_QUERY")

        assert mock_search.call_count == 2
        assert mock_rw.call_count == 1  # stopped after attempt 1, never tried a 2nd rewrite
        assert result is attempt0

    @pytest.mark.asyncio
    async def test_similarity_gain_lost_when_citations_stay_empty(self, monkeypatch):
        """AC-2026-015 interaction (QA note, see design.md control-flow comment + pipeline.py
        `retrieve_with_retry` inline comment): both attempts decline with empty citations, so
        the "same chunk_ids" (empty-set==empty-set) branch fires even though attempt 1's
        best_similarity (0.3) is strictly better than attempt 0's (0.1). The loop stops
        immediately — attempt 1 is NEVER promoted to `best` — and a would-be attempt 2
        (cap=2 still has budget) never runs."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 2)
        attempt0 = _result(
            declined=True,
            query_vector=[0.1],
            best_similarity=0.1,
            citations=[],  # Layer-1 decline always has empty citations
            decline_reason="layer1_guard",
        )
        attempt1 = _result(
            declined=True,
            query_vector=[0.1],
            best_similarity=0.3,  # strictly better than attempt0
            citations=[],  # also empty — still below threshold, still declined
        )
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                side_effect=[attempt0, attempt1],
            ) as mock_search,
            patch(
                "services.ai.AIGateway.rewrite_query",
                new_callable=AsyncMock,
                return_value=_rw("rewritten"),
            ) as mock_rw,
        ):
            result = await retrieve_with_retry(_mock_db(), "query", intent="INFO_QUERY")

        assert mock_search.call_count == 2  # stopped after attempt 1 — attempt 2 never ran
        assert mock_rw.call_count == 1
        # best-seen returned is attempt 0 (0.1), NOT attempt 1's improved 0.3 — see docstring.
        assert result.best_similarity == 0.1
        assert result is attempt0

    @pytest.mark.asyncio
    async def test_rewriter_exception_aborts_to_best(self, monkeypatch):
        """AC-2026-011, AC-2026-018: rewriter raising mid-loop aborts to best-seen, no crash."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 2)
        attempt0 = _result(
            declined=True, query_vector=[0.1], best_similarity=0.2, decline_reason="layer1_guard"
        )
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                return_value=attempt0,
            ) as mock_search,
            patch(
                "services.ai.AIGateway.rewrite_query",
                new_callable=AsyncMock,
                side_effect=Exception("rewriter timeout"),
            ),
        ):
            result = await retrieve_with_retry(_mock_db(), "query")

        assert result is attempt0
        mock_search.assert_called_once()  # no re-search attempted after rewriter failure

    @pytest.mark.asyncio
    async def test_search_and_retrieve_exception_mid_loop_aborts(self, monkeypatch):
        """AC-2026-018: search_and_retrieve raising on a retry attempt aborts to best-seen,
        no partial/corrupt state."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 2)
        attempt0 = _result(
            declined=True, query_vector=[0.1], best_similarity=0.2, decline_reason="layer1_guard"
        )
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                side_effect=[attempt0, Exception("db connection lost")],
            ) as mock_search,
            patch(
                "services.ai.AIGateway.rewrite_query",
                new_callable=AsyncMock,
                return_value=_rw("rewritten"),
            ),
        ):
            result = await retrieve_with_retry(_mock_db(), "query", intent="INFO_QUERY")

        assert result is attempt0
        assert mock_search.call_count == 2

    @pytest.mark.asyncio
    async def test_fts_truncation_reused_for_rewritten_query(self):
        """AC-2026-024: search_and_retrieve (called again per retry attempt) re-applies the
        500-word FTS truncation to whatever query it is given — including a rewritten one."""
        from services.rag.pipeline import search_and_retrieve

        long_query = " ".join(["từ"] * 600)  # 600 words > 500-word cap
        captured: dict = {}

        async def _fake_hybrid_search_rrf(db, query_vector, query_text, top_k):
            captured["query_text"] = query_text
            return []

        with (
            patch(
                "services.rag.pipeline.hybrid_search_rrf",
                new=_fake_hybrid_search_rrf,
            ),
            patch(
                "services.ai.AIGateway.embed",
                new_callable=AsyncMock,
                return_value=[[0.1] * 8],
            ),
        ):
            await search_and_retrieve(_mock_db(), long_query, intent="INFO_QUERY")

        assert len(captured["query_text"].split()) == 500


# ═══════════════════════════════════════════════════════════════════════════
# Observability + cache isolation — AC-2026-021, AC-2026-023
# ═══════════════════════════════════════════════════════════════════════════


class TestObservabilityAndCacheIsolation:
    @pytest.mark.asyncio
    async def test_retry_trace_written_once_per_attempt(self, monkeypatch):
        """AC-2026-021: each retry attempt writes exactly one model_trace row."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 1)
        attempt0 = _result(
            declined=True, query_vector=[0.1], best_similarity=0.1, decline_reason="layer1_guard"
        )
        attempt1 = _result(declined=False, query_vector=[0.1], best_similarity=0.9)
        db = _mock_db()
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                side_effect=[attempt0, attempt1],
            ),
            patch(
                "services.ai.AIGateway.rewrite_query",
                new_callable=AsyncMock,
                return_value=_rw("rewritten"),
            ),
        ):
            await retrieve_with_retry(db, "query", intent="INFO_QUERY")

        assert db.add.call_count == 1
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retry_trace_metadata_has_no_pii_or_tokens(self):
        """AC-2026-021: per-attempt trace stores only attempt#, rewrite, guard, similarity,
        category — no token counts, no PII fields."""
        db = _mock_db()
        await _write_retry_trace(
            db=db,
            attempt=1,
            rewritten_query="tìm áo khoác nam giá rẻ",
            guard_decision="RETRY",
            best_similarity=0.32,
            query_category="short",
        )

        db.add.assert_called_once()
        trace_row = db.add.call_args.args[0]
        assert trace_row.metadata_ == {
            "guard_decision": "RETRY",
            "attempt": 1,
            "rewritten_query": "tìm áo khoác nam giá rẻ",
            "best_similarity": 0.32,
            "query_category": "short",
        }
        assert trace_row.prompt_tokens == 0
        assert trace_row.completion_tokens == 0
        assert trace_row.total_tokens == 0
        # No token/secret-shaped keys anywhere in the persisted metadata.
        for key in trace_row.metadata_:
            assert "token" not in key.lower()
            assert "secret" not in key.lower()
            assert "password" not in key.lower()

    @pytest.mark.asyncio
    async def test_retry_trace_write_failure_is_non_blocking(self):
        """Best-effort: a DB failure while writing the retry trace must not raise."""
        db = _mock_db()
        db.flush.side_effect = Exception("db unavailable")

        await _write_retry_trace(
            db=db,
            attempt=1,
            rewritten_query="query",
            guard_decision="RETRY",
            best_similarity=0.1,
            query_category="short",
        )  # must not raise

    @pytest.mark.asyncio
    async def test_no_cache_write_inside_retry_loop(self, monkeypatch):
        """AC-2026-023: retrieve_with_retry NEVER writes to the semantic cache — cache write
        only happens in the answer path, after acceptance."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 2)
        attempt0 = _result(
            declined=True, query_vector=[0.1], best_similarity=0.1, decline_reason="layer1_guard"
        )
        attempt1 = _result(declined=False, query_vector=[0.1], best_similarity=0.9)
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                side_effect=[attempt0, attempt1],
            ),
            patch(
                "services.ai.AIGateway.rewrite_query",
                new_callable=AsyncMock,
                return_value=_rw("rewritten"),
            ),
            patch("services.rag.pipeline.set_cache", new_callable=AsyncMock) as mock_set_cache,
        ):
            result = await retrieve_with_retry(_mock_db(), "query", intent="INFO_QUERY")

        assert result is attempt1
        mock_set_cache.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# COMPARISON mutual exclusion — ADR-004
# ═══════════════════════════════════════════════════════════════════════════


class TestComparisonMutualExclusion:
    @pytest.mark.asyncio
    async def test_comparison_intent_never_loops(self, monkeypatch):
        """AC-2026-020: COMPARISON intent single-passes even when declined and budget remains —
        the existing split fallback (retrieval_node) handles COMPARISON recovery instead."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 2)
        declined = _result(
            declined=True, query_vector=[0.1], best_similarity=0.1, decline_reason="layer1_guard"
        )
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                return_value=declined,
            ) as mock_search,
            patch("services.ai.AIGateway.rewrite_query", new_callable=AsyncMock) as mock_rw,
        ):
            result = await retrieve_with_retry(_mock_db(), "A và B", intent="COMPARISON")

        assert result is declined
        mock_search.assert_called_once()
        mock_rw.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# D2 kill-switch decline-text parity (RISK-005) — answer_with_rag
# ═══════════════════════════════════════════════════════════════════════════


class TestKillSwitchDeclineParityD2:
    """Design Deviation D2: `decline_reason` lets `answer_with_rag` reproduce the EXACT
    pre-existing decline text + model_used per reason, so RAG_RETRY_MAX_ATTEMPTS=0 stays
    byte-identical to the pre-change single-pass behavior."""

    @pytest.mark.asyncio
    async def test_spam_decline_text_unchanged(self, monkeypatch):
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 0)
        spam = _result(declined=True, query_vector=[], decline_reason="spam")
        with patch(
            "services.rag.pipeline.search_and_retrieve",
            new_callable=AsyncMock,
            return_value=spam,
        ):
            result = await answer_with_rag(_mock_db(), "asdkjh qwer")

        assert result.answer == "Vui lòng đặt câu hỏi liên quan đến sản phẩm hoặc dịch vụ."
        assert result.declined is True
        assert result.model_used == "guard"

    @pytest.mark.asyncio
    async def test_embed_unavailable_decline_text_unchanged(self, monkeypatch):
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 0)
        embed_down = _result(declined=True, query_vector=[], decline_reason="embed_unavailable")
        with patch(
            "services.rag.pipeline.search_and_retrieve",
            new_callable=AsyncMock,
            return_value=embed_down,
        ):
            result = await answer_with_rag(_mock_db(), "query", model="economy-chat")

        assert result.answer == "Dịch vụ tạm thời không khả dụng. Vui lòng thử lại sau."
        assert result.declined is True
        assert result.model_used == "economy-chat"

    @pytest.mark.asyncio
    async def test_layer1_guard_decline_text_unchanged(self, monkeypatch):
        from services.rag.constants import DECLINE_MESSAGE

        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 0)
        layer1 = _result(
            declined=True,
            query_vector=[0.1],
            best_similarity=0.2,
            decline_reason="layer1_guard",
        )
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                return_value=layer1,
            ),
            patch(
                "services.rag.pipeline._write_model_trace", new_callable=AsyncMock
            ) as mock_trace,
        ):
            result = await answer_with_rag(_mock_db(), "query", model="economy-chat")

        assert result.answer == DECLINE_MESSAGE
        assert result.declined is True
        assert result.model_used == "economy-chat"
        mock_trace.assert_called_once()  # REJECTED guard_decision trace preserved (FR-010)

    @pytest.mark.asyncio
    async def test_kill_switch_never_calls_rewriter(self, monkeypatch):
        """AC-2026-014: at max=0, retrieval never touches AIGateway.rewrite_query."""
        monkeypatch.setattr(settings, "RAG_RETRY_MAX_ATTEMPTS", 0)
        layer1 = _result(
            declined=True,
            query_vector=[0.1],
            best_similarity=0.2,
            decline_reason="layer1_guard",
        )
        with (
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new_callable=AsyncMock,
                return_value=layer1,
            ),
            patch("services.ai.AIGateway.rewrite_query", new_callable=AsyncMock) as mock_rw,
        ):
            await answer_with_rag(_mock_db(), "query")

        mock_rw.assert_not_called()
