"""Why this exists: WP-V2-0 — the eval gate grades deterministically; these tests
pin that grading logic (no DB, no LLM, no network).
What it does: unit tests for the pure helpers in scripts/eval_gate.py —
Tier-R recall grading, Tier-F rule grading (must_decline / expected_price /
absent_terms), digit normalization, JSONL resume, and baseline comparison.
"""

from __future__ import annotations

import json

import pytest

from scripts.eval_gate import (
    append_result,
    compare_to_baseline,
    dataset_hash,
    grade_tier_f,
    grade_tier_r,
    is_retryable,
    load_completed,
    normalize_digits,
    summarize,
    uses_graph_path,
)

# ── normalize_digits ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected_substring"),
    [
        ("Giá 24.990.000₫", "24990000"),
        ("costs 24,990,000 VND", "24990000"),
        ("24 990 000 đồng", "24990000"),
        ("giá 6.990.000đ nhé", "6990000"),
    ],
)
def test_normalize_digits_collapses_group_separators(text: str, expected_substring: str):
    assert expected_substring in normalize_digits(text)


def test_normalize_digits_leaves_words_alone():
    assert normalize_digits("RTX 4070 Super") == "RTX 4070 Super"


# ── grade_tier_r ─────────────────────────────────────────────────────────────


def test_tier_r_all_expected_found_passes():
    grade = grade_tier_r(["A-1", "B-2"], ["b-2", "C-3", "A-1"])
    assert grade["passed"] is True
    assert grade["recall"] == 1.0


def test_tier_r_partial_match_fails_with_all_semantics():
    grade = grade_tier_r(["A-1", "B-2"], ["A-1", "C-3"])
    assert grade["passed"] is False
    assert grade["recall"] == 0.5


def test_tier_r_any_semantics_passes_on_single_hit():
    grade = grade_tier_r(["A-1", "B-2", "C-3"], ["C-3"], match="any")
    assert grade["passed"] is True


def test_tier_r_nothing_found_fails():
    assert grade_tier_r(["A-1"], [])["passed"] is False


# ── grade_tier_f ─────────────────────────────────────────────────────────────


def test_tier_f_must_decline_passes_when_declined():
    grade = grade_tier_f({"must_decline": True}, "Xin lỗi, không có thông tin.", True, [])
    assert grade["passed"] is True


def test_tier_f_must_decline_fails_when_answered():
    grade = grade_tier_f({"must_decline": True}, "Tủ lạnh giá 5.000.000₫", False, [{"sku": "X"}])
    assert grade["passed"] is False


def test_tier_f_expected_price_matches_any_format():
    case = {"expected_price": 24990000}
    ans = "Samsung Galaxy S24 Ultra hiện có giá 24,990,000 VND ạ."
    assert grade_tier_f(case, ans, False, [{"sku": "PHONE-SM-001"}])["passed"] is True


def test_tier_f_expected_price_fails_on_decline_or_wrong_price():
    case = {"expected_price": 24990000}
    assert grade_tier_f(case, "Xin lỗi.", True, [])["passed"] is False
    assert grade_tier_f(case, "Giá 19.990.000₫", False, [{"sku": "X"}])["passed"] is False


def test_tier_f_hallucination_trap_fails_on_fabricated_price():
    case = {"absent_terms": ["macbook air"]}
    ans = "Dạ MacBook Air M3 đang có giá 27.990.000₫. Anh lấy màu gì ạ?"
    assert grade_tier_f(case, ans, False, [])["passed"] is False


def test_tier_f_hallucination_trap_passes_on_polite_redirect():
    case = {"absent_terms": ["macbook air"]}
    ans = (
        "Bên em không kinh doanh MacBook Air ạ. "
        "Anh tham khảo MacBook Pro 16 M3 Pro giá 54.990.000₫ nhé."
    )
    assert grade_tier_f(case, ans, False, [{"sku": "LAPTOP-MACBOOK-001"}])["passed"] is True


def test_tier_f_hallucination_trap_passes_on_decline():
    case = {"absent_terms": ["iphone 14"]}
    assert grade_tier_f(case, "Xin lỗi, em không có thông tin.", True, [])["passed"] is True


def test_tier_f_plain_case_requires_answer_and_citations():
    assert grade_tier_f({}, "answer", False, [{"sku": "A"}])["passed"] is True
    assert grade_tier_f({}, "answer", False, [])["passed"] is False
    assert grade_tier_f({}, "declined", True, [])["passed"] is False


# ── resume (JSONL checkpoint) ────────────────────────────────────────────────


def test_resume_roundtrip_and_dataset_hash_isolation(tmp_path):
    jsonl = tmp_path / "tier-r.jsonl"
    append_result(jsonl, {"id": "a", "dataset_hash": "h1", "passed": True})
    append_result(jsonl, {"id": "b", "dataset_hash": "h1", "passed": False})
    append_result(jsonl, {"id": "stale", "dataset_hash": "OLD", "passed": True})

    completed = load_completed(jsonl, "h1")
    assert set(completed) == {"a", "b"}  # stale-hash record ignored
    assert load_completed(tmp_path / "missing.jsonl", "h1") == {}


def test_resume_skips_corrupt_lines(tmp_path):
    jsonl = tmp_path / "tier-f.jsonl"
    jsonl.write_text('{"id": "ok", "dataset_hash": "h", "passed": true}\nnot-json\n')
    assert set(load_completed(jsonl, "h")) == {"ok"}


def test_dataset_hash_changes_with_content():
    assert dataset_hash(b"a") != dataset_hash(b"b")
    assert len(dataset_hash(b"a")) == 12


# ── summarize + baseline compare ─────────────────────────────────────────────


def _results(passed: int, failed: int, category: str = "pricing") -> list[dict]:
    return [
        {"id": f"p{i}", "category": category, "passed": i < passed} for i in range(passed + failed)
    ]


def test_summarize_per_category():
    summary = summarize(_results(3, 1))
    assert summary["pass_rate"] == 0.75
    assert summary["by_category"]["pricing"] == {"total": 4, "passed": 3, "pass_rate": 0.75}


def test_baseline_regression_beyond_threshold_fails_gate():
    baseline = {"summary": {"pass_rate": 0.90}}
    verdict = compare_to_baseline(summarize(_results(8, 2)), baseline, threshold_pp=2.0)
    assert verdict["regressed"] is True  # 80% vs 90% = -10pp


def test_baseline_small_dip_within_threshold_passes():
    baseline = {"summary": {"pass_rate": 0.81}}
    verdict = compare_to_baseline(summarize(_results(8, 2)), baseline, threshold_pp=2.0)
    assert verdict["regressed"] is False  # -1pp only


def test_baseline_improvement_passes():
    baseline = {"summary": {"pass_rate": 0.5}}
    verdict = compare_to_baseline(summarize(_results(10, 0)), baseline, threshold_pp=2.0)
    assert verdict["regressed"] is False
    assert verdict["delta_pp"] == 50.0


# ── retryable classification ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("message", "retryable"),
    [
        ("RateLimitError: 429 Too Many Requests", True),
        ("model overloaded, please retry", True),
        ("Read timeout", True),
        ("Invalid API key", False),
        ("connection refused", False),
    ],
)
def test_is_retryable(message: str, retryable: bool):
    assert is_retryable(Exception(message)) is retryable


def test_gold_dataset_shape():
    """The committed dataset must stay gradable: unique ids, valid tiers."""
    from pathlib import Path

    cases = json.loads(Path("tests/eval/gold_dataset.json").read_text())
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    assert len(cases) >= 40
    tier_r = [c for c in cases if c.get("expected_skus")]
    tier_f = [c for c in cases if c.get("tier_f")]
    assert len(tier_r) >= 25, "Tier-R needs a healthy retrieval set"
    assert 10 <= len(tier_f) <= 15, "Tier-F must stay a small smoke set"
    for c in cases:
        graded = bool(
            c.get("expected_skus")
            or c.get("must_decline")
            or c.get("expected_price")
            or c.get("absent_terms")
        )
        assert graded, f"{c['id']} has no deterministic expectation"


# ── uses_graph_path (WP-V3-3) ────────────────────────────────────────────────


def test_multi_intent_cases_route_through_graph_path():
    """WP-V3-3: multi_intent must run the production graph path (decomposition
    lives there); every other category keeps the direct answer_with_rag call."""
    assert uses_graph_path({"id": "mi_001", "category": "multi_intent"}) is True
    assert uses_graph_path({"id": "pr_001", "category": "pricing"}) is False
    assert uses_graph_path({"id": "ht_001", "category": "hallucination_trap"}) is False
    assert uses_graph_path({"id": "x", "category": None}) is False
    assert uses_graph_path({"id": "x"}) is False
