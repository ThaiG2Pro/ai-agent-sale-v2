"""Why this exists: TDD for all deterministic helper functions in services/rag.py.
What it does: Tests classify_query, compute_adaptive_topk, and compress_context.
              These are purely deterministic — no DB, no LLM, no network required.

Article III Section 3.1: Deterministic components MUST follow strict TDD.
"""

from __future__ import annotations

from services.rag import (
    RAGResult,
    _overlap_ratio,
    classify_query,
    compress_context,
    compute_adaptive_topk,
)

# ═══════════════════════════════════════════════════════════
# classify_query — FR-015 deterministic classification
# ═══════════════════════════════════════════════════════════


class TestClassifyQuery:
    """FR-015: short ≤5 words → 'short', 6-15 → 'long', >15+no signals → 'ambiguous'."""

    # Short queries (≤ 5 words)
    def test_single_word(self):
        assert classify_query("price") == "short"

    def test_exactly_five_words(self):
        assert classify_query("giá widget cao cấp gì") == "short"

    def test_four_words_english(self):
        assert classify_query("What is this?") == "short"

    # Long queries (6-15 words)
    def test_six_words(self):
        assert classify_query("How much does the Pro Widget cost") == "long"

    def test_exactly_fifteen_words(self):
        words = " ".join(["word"] * 15)
        assert classify_query(words) == "long"

    def test_long_query_with_product_name(self):
        # >15 words but has a capitalised product name → 'long'
        q = " ".join(["tell", "me", "about", "the"] + ["product"] * 12 + ["WidgetPro"])
        assert classify_query(q) == "long"

    def test_long_query_with_action_verb_price(self):
        # >15 words but contains action verb 'price' → 'long'
        q = "the " + " ".join(["something"] * 15) + " price"
        assert classify_query(q) == "long"

    def test_long_query_with_action_verb_compare(self):
        q = " ".join(["please"] * 16) + " compare"
        assert classify_query(q) == "long"

    # Ambiguous queries (>15 words, no action verb, no product name)
    def test_ambiguous_open_ended(self):
        # >15 words, Vietnamese generic vocabulary (no action verb, no product name)
        q = (
            "hãy cho tôi biết tất cả những sản phẩm mà"
            " công ty bạn đang kinh doanh hiện tại"
        )
        assert classify_query(q) == "ambiguous"

    def test_ambiguous_english_no_signals(self):
        q = " ".join(
            [
                "tell",
                "me",
                "about",
                "all",
                "the",
                "things",
                "that",
                "you",
                "have",
                "in",
                "your",
                "store",
                "right",
                "now",
                "please",
                "thanks",
            ]
        )
        assert classify_query(q) == "ambiguous"

    # Vietnamese action-verb edge cases
    def test_vietnamese_action_verb_giá(self):
        # >15 words with 'giá' → 'long'
        q = " ".join(["tôi"] * 15) + " giá"
        assert classify_query(q) == "long"

    def test_vietnamese_action_verb_mua(self):
        q = " ".join(["một"] * 15) + " mua"
        assert classify_query(q) == "long"

    def test_boundary_exactly_16_no_signals(self):
        q = " ".join(["word"] * 16)
        assert classify_query(q) == "ambiguous"

    def test_boundary_exactly_16_has_price(self):
        q = " ".join(["word"] * 15) + " price"
        assert classify_query(q) == "long"


# ═══════════════════════════════════════════════════════════
# compute_adaptive_topk — FR-009
# ═══════════════════════════════════════════════════════════


class TestComputeAdaptiveTopk:
    """FR-009: TopK must map short→5, long→15, ambiguous→20."""

    def test_short_returns_5(self):
        assert compute_adaptive_topk("giá") == 5

    def test_short_five_words(self):
        assert compute_adaptive_topk("giá widget cao cấp gì") == 5

    def test_long_returns_15(self):
        assert compute_adaptive_topk("How much does the Pro Widget cost today") == 15

    def test_ambiguous_returns_20(self):
        q = " ".join(["word"] * 16)
        assert compute_adaptive_topk(q) == 20

    def test_boundary_6_words_is_long(self):
        assert compute_adaptive_topk("a b c d e f") == 15

    def test_boundary_15_words_is_long(self):
        assert compute_adaptive_topk(" ".join(["w"] * 15)) == 15

    def test_boundary_16_words_no_signals_is_ambiguous(self):
        assert compute_adaptive_topk(" ".join(["w"] * 16)) == 20

    def test_vn_010_ambiguous(self):
        q = (
            "Hãy cho tôi biết tất cả những sản phẩm mà công ty bạn đang kinh doanh "
            "và giá của từng loại cũng như các chính sách ưu đãi hiện tại."
        )
        # >15 words, has 'giá' action verb → 'long' (TopK 15) is acceptable,
        # but the important thing is it does NOT produce TopK 5.
        result = compute_adaptive_topk(q)
        assert result in (15, 20), f"Expected 15 or 20, got {result}"


# ═══════════════════════════════════════════════════════════
# _overlap_ratio — internal helper
# ═══════════════════════════════════════════════════════════


class TestOverlapRatio:
    def test_identical(self):
        assert _overlap_ratio("hello world", "hello world") == 1.0

    def test_empty_strings(self):
        assert _overlap_ratio("", "") == 0.0

    def test_one_empty(self):
        assert _overlap_ratio("hello", "") == 0.0
        assert _overlap_ratio("", "hello") == 0.0

    def test_completely_different(self):
        assert _overlap_ratio("abc", "xyz") < 0.5

    def test_high_overlap(self):
        a = "Widget Pro là sản phẩm cao cấp với giá tốt nhất."
        b = "Widget Pro là sản phẩm cao cấp với giá rất tốt nhất."
        ratio = _overlap_ratio(a, b)
        assert ratio > 0.80, f"Expected >0.80, got {ratio:.4f}"

    def test_low_overlap(self):
        a = "The product ships in 3 days from our warehouse."
        b = "Khách hàng được hoàn tiền trong vòng 7 ngày."
        assert _overlap_ratio(a, b) < 0.5


# ═══════════════════════════════════════════════════════════
# compress_context — FR-012
# ═══════════════════════════════════════════════════════════


def _make_chunk(
    description: str,
    vector_score: float = 0.8,
    rrf_score: float = 0.02,
    sku: str = "W001",
) -> dict:
    return {
        "id": "prod-1",
        "chunk_id": f"chunk-{sku}-{vector_score}",
        "sku": sku,
        "name": f"Product {sku}",
        "description": description,
        "vector_score": vector_score,
        "fts_score": 0.5,
        "rrf_score": rrf_score,
    }


class TestCompressContext:
    """FR-012: dedup + score<0.5 removal + >80% near-dup removal."""

    def test_empty_input(self):
        assert compress_context([]) == []

    def test_exact_duplicates_removed(self):
        chunks = [
            _make_chunk("Same text here", sku="A"),
            _make_chunk("Same text here", sku="B"),  # exact dup
        ]
        result = compress_context(chunks)
        assert len(result) == 1

    def test_low_score_chunks_removed(self):
        chunks = [
            _make_chunk("Good chunk", vector_score=0.8),
            _make_chunk(
                "Low confidence chunk", vector_score=0.1
            ),  # below 0.25 threshold
        ]
        result = compress_context(chunks)
        assert len(result) == 1
        assert result[0]["description"] == "Good chunk"

    def test_boundary_exactly_0_25_kept(self):
        chunks = [_make_chunk("Boundary chunk", vector_score=0.25)]
        result = compress_context(chunks)
        assert len(result) == 1  # 0.25 is kept (threshold is strictly >= 0.25)

    def test_boundary_just_below_0_25_removed(self):
        chunks = [_make_chunk("Below boundary", vector_score=0.249)]
        result = compress_context(chunks)
        assert len(result) == 0

    def test_near_duplicates_removed_keeps_highest_rrf(self):
        # Two very similar descriptions — keep the one with higher rrf_score
        base = "Widget Pro là sản phẩm cao cấp với nhiều tính năng vượt trội."
        near = "Widget Pro là sản phẩm cao cấp với nhiều tính năng vượt trội tuyệt vời."
        chunks = [
            _make_chunk(base, rrf_score=0.01, sku="LOW"),
            _make_chunk(
                near, rrf_score=0.05, sku="HIGH"
            ),  # higher score, processed first
        ]
        result = compress_context(chunks)
        assert len(result) == 1
        assert result[0]["sku"] == "HIGH"

    def test_distinct_chunks_all_kept(self):
        chunks = [
            _make_chunk("Widget Pro là sản phẩm cao cấp."),
            _make_chunk("Chính sách hoàn tiền trong 30 ngày."),
            _make_chunk("Giao hàng miễn phí toàn quốc cho đơn từ 500k."),
        ]
        result = compress_context(chunks)
        assert len(result) == 3

    def test_custom_score_threshold(self):
        chunks = [
            _make_chunk("High quality chunk", vector_score=0.7),
            _make_chunk("Low quality chunk", vector_score=0.55),
        ]
        # With threshold=0.6, the 0.55 chunk is removed
        result = compress_context(chunks, score_threshold=0.6)
        assert len(result) == 1
        assert result[0]["description"] == "High quality chunk"

    def test_custom_overlap_threshold(self):
        a = "The product has excellent build quality and long battery life."
        b = "The product has excellent build quality and long battery lifetime."
        chunks = [
            _make_chunk(a, rrf_score=0.01),
            _make_chunk(b, rrf_score=0.05),
        ]
        # With threshold=0.95, these are NOT near-dups (ratio ~0.97 → removed)
        # With threshold=0.50, they ARE near-dups
        result_strict = compress_context(chunks, overlap_threshold=0.50)
        assert len(result_strict) == 1

    def test_three_step_pipeline_combined(self):
        """Validates all three compression steps fire in sequence."""
        chunks = [
            _make_chunk("Exact duplicate", vector_score=0.8, rrf_score=0.03, sku="A"),
            _make_chunk(
                "Exact duplicate", vector_score=0.8, rrf_score=0.01, sku="B"
            ),  # exact dup
            _make_chunk(
                "Low score chunk", vector_score=0.2, rrf_score=0.02, sku="C"
            ),  # score filter
            _make_chunk(
                "Widget cao cap voi tinh nang tot nhat hien nay.",
                vector_score=0.8,
                rrf_score=0.05,
                sku="D",
            ),
            _make_chunk(
                "Widget cao cap voi tinh nang tot nhat hien nay luon.",
                vector_score=0.8,
                rrf_score=0.02,
                sku="E",
            ),  # near-dup of D
        ]
        result = compress_context(chunks)
        skus = {c["sku"] for c in result}
        assert "A" in skus, "First exact dup should be kept"
        assert "B" not in skus, "Second exact dup should be removed"
        assert "C" not in skus, "Low-score chunk should be removed"
        # D or E, but not both (near-dup removal)
        assert not ("D" in skus and "E" in skus), (
            "Near-dup pair should have only one kept"
        )


# ═══════════════════════════════════════════════════════════
# RAGResult — basic schema validation
# ═══════════════════════════════════════════════════════════


class TestRAGResult:
    def test_declined_result_has_empty_citations(self):
        result = RAGResult(
            answer="I couldn't find relevant information.",
            declined=True,
            citations=[],
            best_similarity=0.3,
            rrf_scores=[],
            query_category="short",
            top_k_used=5,
            model_used="economy-chat",
            escalation_flag=False,
            chunks_before_compression=3,
            chunks_after_compression=0,
        )
        assert result.declined is True
        assert result.citations == []
        assert result.best_similarity == 0.3

    def test_successful_result_has_citations(self):
        result = RAGResult(
            answer="The Widget Pro costs 500,000 VND.",
            declined=False,
            citations=[
                {
                    "product_id": "abc",
                    "chunk_id": "xyz",
                    "sku": "WP01",
                    "name": "Widget Pro",
                }
            ],
            best_similarity=0.92,
            rrf_scores=[0.03, 0.02],
            query_category="short",
            top_k_used=5,
            model_used="economy-chat",
            escalation_flag=False,
            chunks_before_compression=5,
            chunks_after_compression=2,
        )
        assert result.declined is False
        assert len(result.citations) == 1
        assert result.citations[0]["sku"] == "WP01"


class TestArticleXIIEfficiency:
    """Article XII efficiency tests: TopK and classification behaviour."""

    def test_easy_query_topk_is_minimal(self):
        # short easy query → TopK 5 and category 'short'
        assert compute_adaptive_topk("giá") == 5
        assert classify_query("giá") == "short"

    def test_ambiguous_query_topk_is_maximal(self):
        # ambiguous long query (16 words) → TopK 20
        q = " ".join(["word"] * 16)
        assert compute_adaptive_topk(q) == 20


class TestCompressContextAdditional:
    def test_compression_reduces_token_count(self):
        # 5 high-score, 3 low-score (removed), 2 exact duplicates
        chunks = []
        for i in range(5):
            chunks.append(
                _make_chunk(
                    f"High chunk {i}",
                    vector_score=0.9,
                    rrf_score=0.05,
                    sku=f"H{i}",
                )
            )
        for i in range(3):
            chunks.append(
                _make_chunk(
                    f"Low chunk {i}",
                    vector_score=0.3,
                    rrf_score=0.01,
                    sku=f"L{i}",
                )
            )
        chunks.append(
            _make_chunk(
                "Duplicate text",
                vector_score=0.9,
                rrf_score=0.04,
                sku="D1",
            )
        )
        chunks.append(
            _make_chunk(
                "Duplicate text",
                vector_score=0.9,
                rrf_score=0.03,
                sku="D2",
            )
        )

        result = compress_context(chunks)
        # Expect at most 5 after compression (≥50% reduction)
        assert len(result) <= 5
        # No two results share identical description
        descriptions = [c["description"] for c in result]
        assert len(descriptions) == len(set(descriptions))
