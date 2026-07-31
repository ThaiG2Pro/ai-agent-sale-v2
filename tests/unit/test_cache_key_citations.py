"""Why this exists: WP-V2-2 unit coverage — deterministic L1 cache key on the
RAW user query + fragment-level citations (FR-011).
What it does: Verifies (1) the L1 lookup keys on raw input and runs BEFORE the
LLM normalize step, so identical raw queries hit regardless of what the
normalizer returns; (2) cache writes key raw so lookups find them; (3)
fragment_text extraction/annotation and the Citation model round-trip.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.agent.state import Citation
from services.rag.fragments import annotate_fragments, extract_fragment
from services.semantic_cache import generate_query_hash

SOURCE_TEXT = (
    "[PHONE-SM-001] Samsung S24 Ultra\n"
    "Giá: 24,990,000 VND\n"
    "Màn hình 6.8 inch Dynamic AMOLED. Camera 200MP zoom 100x. Pin 5000mAh sạc nhanh 45W."
)


# ═══════════════════════════════════════════════════════════════════════════
# extract_fragment / annotate_fragments
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractFragment:
    def test_picks_best_matching_sentence(self):
        answer = "Dạ Samsung S24 Ultra có giá 24,990,000 VND ạ. Camera 200MP zoom 100x rất nét."
        fragment = extract_fragment(answer, SOURCE_TEXT)
        assert fragment is not None
        assert "200MP" in fragment or "24,990,000" in fragment

    def test_returns_none_when_no_overlap(self):
        assert extract_fragment("Hôm nay trời đẹp quá nhỉ", SOURCE_TEXT) is None

    def test_empty_inputs_return_none(self):
        assert extract_fragment("", SOURCE_TEXT) is None
        assert extract_fragment("câu trả lời", "") is None

    def test_deterministic(self):
        answer = "Pin 5000mAh sạc nhanh 45W dùng cả ngày ạ."
        assert extract_fragment(answer, SOURCE_TEXT) == extract_fragment(answer, SOURCE_TEXT)


class TestAnnotateFragments:
    def test_adds_fragment_text_key_and_does_not_mutate(self):
        citations = [
            {
                "product_id": "p1",
                "chunk_id": "c1",
                "sku": "PHONE-SM-001",
                "name": "Samsung S24 Ultra",
                "source_text": SOURCE_TEXT,
            }
        ]
        annotated = annotate_fragments(citations, "Camera 200MP zoom 100x ạ.")
        assert "fragment_text" in annotated[0]
        assert annotated[0]["fragment_text"] is not None
        assert "fragment_text" not in citations[0]  # non-mutating

    def test_no_match_yields_none_field(self):
        citations = [{"source_text": SOURCE_TEXT}]
        annotated = annotate_fragments(citations, "xyz")
        assert annotated[0]["fragment_text"] is None


class TestCitationModel:
    def test_fragment_text_optional_defaults_none(self):
        c = Citation(product_id="p", chunk_id="c", sku="S", name="N", source_text="t")
        assert c.fragment_text is None

    def test_cached_dict_with_fragment_round_trips(self):
        """retrieval_node does Citation(**cached_dict) inside try/except — a cached
        citation carrying fragment_text must NOT raise (or it is silently dropped)."""
        c = Citation(
            product_id="p",
            chunk_id="c",
            sku="S",
            name="N",
            source_text="t",
            fragment_text="frag",
        )
        assert c.fragment_text == "frag"


# ═══════════════════════════════════════════════════════════════════════════
# L1 cache key on RAW query — WP-V2-2 core fix
# ═══════════════════════════════════════════════════════════════════════════


def _normalized(canonical: str):
    return SimpleNamespace(
        canonical=canonical,
        extracted_keywords=["samsung", "s24"],
        detected_language="vi",
        intent="PRICING",
        is_valid=True,
    )


def _chunk():
    return {
        "id": "p1",
        "chunk_id": "c1",
        "sku": "PHONE-SM-001",
        "name": "Samsung S24 Ultra",
        "description": "Màn hình 6.8 inch. Camera 200MP. Pin 5000mAh.",
        "price": 24_990_000,
        "vector_score": 0.9,
        "rrf_score": 0.9,
    }


def _verdict():
    return SimpleNamespace(answerable=True, supported=True, unsupported_claims=[])


def _llm(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=None
    )


def _l1_store():
    """Stateful in-memory L1 stand-in keyed exactly like the real table (hash of query)."""
    store: dict[str, dict] = {}

    async def fake_get(db, query):
        return store.get(generate_query_hash(query))

    async def fake_set(db, query, response, embedding, model_name, citations=None):
        store[generate_query_hash(query)] = {"response": response, "citations": citations or []}

    return store, fake_get, fake_set


class TestL1RawQueryKey:
    @pytest.mark.asyncio
    async def test_same_raw_query_hits_l1_despite_normalize_variants(self):
        """WP-V2-2 acceptance: 2 calls with the same raw query → second is an L1 hit
        even though the LLM normalizer returns a DIFFERENT canonical each time."""
        import services.rag.pipeline as pipeline_mod

        store, fake_get, fake_set = _l1_store()
        raw = "Giá Samsung S24 Ultra bao nhiêu?"
        db = AsyncMock()

        with (
            patch.object(pipeline_mod, "get_l1_cache", new=fake_get),
            patch.object(pipeline_mod, "set_cache", new=fake_set),
            patch.object(pipeline_mod, "get_l2_cache", new=AsyncMock(return_value=None)),
            patch.object(
                pipeline_mod.AIGateway,
                "normalize_query",
                new=AsyncMock(
                    side_effect=[_normalized("giá samsung s24"), _normalized("samsung s24 giá")]
                ),
            ) as mock_norm,
            patch.object(pipeline_mod.AIGateway, "embed", new=AsyncMock(return_value=[[0.1] * 4])),
            patch.object(
                pipeline_mod, "hybrid_search_rrf", new=AsyncMock(return_value=[_chunk()])
            ),
            patch.object(
                pipeline_mod.AIGateway,
                "complete",
                new=AsyncMock(return_value=_llm("Giá 24,990,000 VND ạ.")),
            ),
            patch(
                "services.rag.groundedness.check_groundedness",
                new=AsyncMock(return_value=_verdict()),
            ),
            patch.object(pipeline_mod, "_write_model_trace", new=AsyncMock()),
        ):
            first = await pipeline_mod.answer_with_rag(db, raw)
            second = await pipeline_mod.answer_with_rag(db, raw)

        assert first.declined is False
        assert second.model_used == "cache"  # L1 hit on identical raw query
        assert second.answer == first.answer
        # normalize ran only for the first call — the L1 hit short-circuits BEFORE it
        assert mock_norm.await_count == 1
        # the write landed under the RAW query hash, not either canonical variant
        assert generate_query_hash(raw) in store
        assert generate_query_hash("giá samsung s24") not in store

    @pytest.mark.asyncio
    async def test_l1_lookup_receives_raw_query(self):
        """search_and_retrieve must hand get_l1_cache the raw query untouched."""
        import services.rag.pipeline as pipeline_mod

        raw = "  Giá iPhame 17 Pro?  "
        hit = {"response": "cached", "citations": []}
        with (
            patch.object(pipeline_mod, "get_l1_cache", new=AsyncMock(return_value=hit)) as mock_l1,
            patch.object(pipeline_mod.AIGateway, "normalize_query", new=AsyncMock()) as mock_norm,
        ):
            result = await pipeline_mod.search_and_retrieve(AsyncMock(), raw)

        mock_l1.assert_awaited_once()
        assert mock_l1.await_args.args[1] == raw
        mock_norm.assert_not_awaited()  # hit costs zero chat calls
        assert result.cached_answer == "cached"
        assert result.canonical_query == raw

    @pytest.mark.asyncio
    async def test_cached_citations_carry_fragment_text(self):
        """Accepted answers are cached WITH fragment-annotated citations, and the
        L1 replay returns them (FR-011 grounding survives the cache)."""
        import services.rag.pipeline as pipeline_mod

        _store, fake_get, fake_set = _l1_store()
        raw = "Camera Samsung S24 Ultra thế nào?"
        db = AsyncMock()

        with (
            patch.object(pipeline_mod, "get_l1_cache", new=fake_get),
            patch.object(pipeline_mod, "set_cache", new=fake_set),
            patch.object(pipeline_mod, "get_l2_cache", new=AsyncMock(return_value=None)),
            patch.object(
                pipeline_mod.AIGateway,
                "normalize_query",
                new=AsyncMock(return_value=_normalized("camera samsung s24")),
            ),
            patch.object(pipeline_mod.AIGateway, "embed", new=AsyncMock(return_value=[[0.1] * 4])),
            patch.object(
                pipeline_mod, "hybrid_search_rrf", new=AsyncMock(return_value=[_chunk()])
            ),
            patch.object(
                pipeline_mod.AIGateway,
                "complete",
                new=AsyncMock(return_value=_llm("Camera 200MP ạ. Pin 5000mAh nữa ạ.")),
            ),
            patch(
                "services.rag.groundedness.check_groundedness",
                new=AsyncMock(return_value=_verdict()),
            ),
            patch.object(pipeline_mod, "_write_model_trace", new=AsyncMock()),
        ):
            live = await pipeline_mod.answer_with_rag(db, raw)
            replay = await pipeline_mod.answer_with_rag(db, raw)

        assert live.citations[0]["fragment_text"] is not None
        assert replay.model_used == "cache"
        assert replay.citations[0]["fragment_text"] == live.citations[0]["fragment_text"]


class TestFailedGenerationNeverCached:
    @pytest.mark.asyncio
    async def test_generation_exception_skips_cache_write(self):
        """Generation failure falls back to DECLINE_MESSAGE with llm_response=None —
        caching that fallback would replay a fake decline for every future hit
        (observed live during Tier-F under Groq 429s)."""
        import services.rag.pipeline as pipeline_mod
        from services.rag.constants import DECLINE_MESSAGE

        store, fake_get, fake_set = _l1_store()
        raw = "Giá Samsung S24 Ultra?"

        with (
            patch.object(pipeline_mod, "get_l1_cache", new=fake_get),
            patch.object(pipeline_mod, "set_cache", new=fake_set),
            patch.object(pipeline_mod, "get_l2_cache", new=AsyncMock(return_value=None)),
            patch.object(
                pipeline_mod.AIGateway,
                "normalize_query",
                new=AsyncMock(return_value=_normalized("giá samsung s24")),
            ),
            patch.object(pipeline_mod.AIGateway, "embed", new=AsyncMock(return_value=[[0.1] * 4])),
            patch.object(
                pipeline_mod, "hybrid_search_rrf", new=AsyncMock(return_value=[_chunk()])
            ),
            patch.object(
                pipeline_mod.AIGateway,
                "complete",
                new=AsyncMock(side_effect=RuntimeError("429 rate limit")),
            ),
            patch.object(pipeline_mod, "_write_model_trace", new=AsyncMock()),
        ):
            result = await pipeline_mod.answer_with_rag(AsyncMock(), raw)

        assert result.answer == DECLINE_MESSAGE
        assert store == {}  # nothing cached — next request retries generation


# ═══════════════════════════════════════════════════════════════════════════
# Graph path: _write_cache keeps the deterministic expanded key + fragments
# ═══════════════════════════════════════════════════════════════════════════


class TestAnswerNodeWriteCache:
    @pytest.mark.asyncio
    async def test_write_cache_keys_canonical_and_annotates_fragments(self):
        """Graph path: canonical_query == pronoun-EXPANDED query (normalize skipped),
        which is what get_l1_cache hashed — raw user_message would poison pronoun
        queries across products. Citations gain fragment_text before the write."""
        from core.agent.nodes.answer import _write_cache

        state = {
            "user_message": "nó giá bao nhiêu",
            "canonical_query": "Samsung S24 Ultra giá bao nhiêu",
            "query_vector": [0.1] * 4,
            "citations": [
                Citation(
                    product_id="p1",
                    chunk_id="c1",
                    sku="PHONE-SM-001",
                    name="Samsung S24 Ultra",
                    source_text=SOURCE_TEXT,
                )
            ],
        }
        with patch("services.semantic_cache.set_cache", new=AsyncMock()) as mock_set:
            await _write_cache(state, "Giá 24,990,000 VND ạ.", AsyncMock())

        mock_set.assert_awaited_once()
        kwargs = mock_set.await_args.kwargs
        assert kwargs["query"] == "Samsung S24 Ultra giá bao nhiêu"
        assert kwargs["citations"][0]["fragment_text"] is not None
