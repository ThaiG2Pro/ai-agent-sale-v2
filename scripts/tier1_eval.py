"""Why this exists: Tier 1 (deterministic heuristics) + Tier 2 (HITL Likert) evaluation.
What it does: Runs gold_dataset.json queries through the full RAG pipeline, applies
             automated keyword checks (Tier 1), then presents results for human 5-point
             Likert grading (Tier 2). Saves results to reports/eval_results.json.

Constitution Article III, Section 3.2 - Lean Tiered Evaluation:
  Tier 1: keyword presence, confidence guard check, citation presence
          (CI-safe, zero-cost).
  Tier 2: human reviewer assigns 1-5 Likert score per answer.
    1 = Completely wrong / irrelevant
    2 = Partially relevant, major errors
    3 = Acceptable, minor gaps
    4 = Good, nearly complete with citations
    5 = Perfectly accurate, complete citations

Usage:
  uv run python scripts/tier1_eval.py
  uv run python scripts/tier1_eval.py --skip-tier2   (CI mode: Tier 1 only)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import anyio
from anyio import Path

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logging import setup_logging
from services.database import AsyncSessionLocal
from services.rag import answer_with_rag

GOLD_DATASET_PATH = "tests/eval/gold_dataset.json"
RESULTS_PATH = "reports/eval_results.json"

_LIKERT_GUIDE = (
    "\n  Likert scale:\n"
    "    1 = Completely wrong / irrelevant\n"
    "    2 = Partially relevant, major errors\n"
    "    3 = Acceptable, minor gaps\n"
    "    4 = Good, nearly complete with citations\n"
    "    5 = Perfectly accurate, complete citations\n"
    "    s = Skip (not graded)\n"
)

_DIVIDER = "─" * 60


async def _read_grade(prompt: str) -> int | None:
    """Reads a 1-5 integer or None ('s') from stdin - off the event loop."""
    while True:
        raw: str = await anyio.to_thread.run_sync(
            lambda: input(prompt).strip().lower()  # noqa: ASYNC250
        )
        if raw == "s":
            return None
        try:
            grade = int(raw)
            if 1 <= grade <= 5:
                return grade
        except ValueError:
            pass
        print("  \u26a0  Enter a number 1-5 or 's' to skip.")


async def evaluate_item(
    db: Any,
    item: dict[str, Any],
    skip_tier2: bool,
    verbose: bool = False,
) -> dict[str, Any]:
    """Runs one gold-dataset item through the full RAG pipeline and grades it."""
    query = item["query"]
    expected_keywords: list[str] = item.get("expected_keywords", [])

    print(f"\n{_DIVIDER}")
    print(f"[{item['id']}] {query}")
    print(_DIVIDER)

    # ── Run full RAG pipeline ─────────────────────────────────────────────────
    try:
        rag_result = await answer_with_rag(db, query)
    except Exception as exc:
        print(f"  ✗ Pipeline error: {exc}")
        return {"id": item["id"], "query": query, "pipeline_error": str(exc)}

    # ── Tier 1: deterministic checks ──────────────────────────────────────────
    answer_lower = rag_result.answer.lower()

    # 1a. Keyword presence in answer
    matched = [kw for kw in expected_keywords if kw.lower() in answer_lower]
    keyword_score = len(matched) / len(expected_keywords) if expected_keywords else 1.0
    tier1_pass = (
        not rag_result.declined and keyword_score > 0 and len(rag_result.citations) > 0
    )

    print(f"  Category  : {rag_result.query_category} (TopK={rag_result.top_k_used})")
    print(
        f"  Similarity: {rag_result.best_similarity:.4f}"
        f" | Declined: {rag_result.declined}"
    )
    print(
        f"  Tier 1    : {'✓ PASS' if tier1_pass else '✗ FAIL'} "
        f"(kw {len(matched)}/{len(expected_keywords)}, "
        f"citations {len(rag_result.citations)})"
    )
    answer_display = rag_result.answer if verbose else rag_result.answer[:400]
    print(f"\n  Answer:\n  {answer_display}")
    if rag_result.citations:
        if verbose:
            for c in rag_result.citations:
                print(f"  Citation: {c}")
        else:
            cites = ", ".join(f"{c['sku']}" for c in rag_result.citations[:3])
            print(f"  Citations : {cites}")
    print(
        f"\n  Compression: {rag_result.chunks_before_compression}→"
        f"{rag_result.chunks_after_compression} chunks"
    )

    # ── Tier 2: human Likert grading ─────────────────────────────────────────
    human_grade: int | None = None
    if not skip_tier2:
        print(_LIKERT_GUIDE)
        human_grade = await _read_grade("  Your grade (1-5 or s): ")
        if human_grade is not None:
            print(f"  Recorded: {human_grade}/5")

    return {
        "id": item["id"],
        "query": query,
        "query_category": rag_result.query_category,
        "top_k_used": rag_result.top_k_used,
        "best_similarity": round(rag_result.best_similarity, 4),
        "declined": rag_result.declined,
        "answer_snippet": rag_result.answer[:200],
        "citations": rag_result.citations,
        "chunks_before": rag_result.chunks_before_compression,
        "chunks_after": rag_result.chunks_after_compression,
        "tier1_keyword_score": round(keyword_score, 4),
        "tier1_pass": tier1_pass,
        "human_grade": human_grade,  # 1-5 or null
    }


async def main(skip_tier2: bool = False, verbose: bool = False) -> None:
    setup_logging()

    gold_path = Path(GOLD_DATASET_PATH)
    if not await gold_path.exists():
        print(f"Error: Gold dataset not found at {GOLD_DATASET_PATH}")
        sys.exit(1)

    dataset: list[dict[str, Any]] = json.loads(await gold_path.read_text())
    os.makedirs("reports", exist_ok=True)

    mode = "Tier 1 only (CI)" if skip_tier2 else "Tier 1 + Tier 2 (HITL)"
    print(f"\n{'═' * 60}")
    print(f"  EVALUATION RUN — {mode}")
    print(f"  Dataset : {GOLD_DATASET_PATH}  ({len(dataset)} cases)")
    print(f"{'═' * 60}")

    results: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as db:
        for item in dataset:
            result = await evaluate_item(
                db, item, skip_tier2=skip_tier2, verbose=verbose
            )
            results.append(result)

    # ── Aggregate metrics ─────────────────────────────────────────────────────
    tier1_passes = sum(1 for r in results if r.get("tier1_pass"))
    avg_keyword = sum(r.get("tier1_keyword_score", 0) for r in results) / len(results)
    avg_similarity = sum(r.get("best_similarity", 0) for r in results) / len(results)
    declined_count = sum(1 for r in results if r.get("declined"))

    graded = [r["human_grade"] for r in results if r.get("human_grade") is not None]
    avg_human = sum(graded) / len(graded) if graded else None

    summary = {
        "total_cases": len(results),
        "tier1_pass_rate": round(tier1_passes / len(results), 4),
        "avg_keyword_score": round(avg_keyword, 4),
        "avg_best_similarity": round(avg_similarity, 4),
        "declined_count": declined_count,
        "tier2_graded_count": len(graded),
        "avg_human_grade": round(avg_human, 2) if avg_human is not None else None,
        "details": results,
    }

    results_path = Path(RESULTS_PATH)
    await results_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    print(f"\n{'═' * 60}")
    print("  EVALUATION COMPLETE")
    print(f"{'═' * 60}")
    print(f"  Cases          : {len(results)}")
    print(
        f"  Tier 1 Pass    : {tier1_passes}/{len(results)}"
        f" ({summary['tier1_pass_rate']:.0%})"
    )
    print(f"  Avg Keyword    : {avg_keyword:.2%}")
    print(f"  Avg Similarity : {avg_similarity:.4f}")
    print(f"  Declined       : {declined_count}")
    if avg_human is not None:
        print(f"  Avg Human Grade: {avg_human:.2f}/5  (n={len(graded)})")
    else:
        print("  Avg Human Grade: n/a (Tier 2 skipped)")
    print(f"\n  Results → {RESULTS_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG Evaluation CLI")
    parser.add_argument(
        "--skip-tier2",
        action="store_true",
        help="Skip human grading (Tier 1 / CI mode only)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show full answer text and all citations (not truncated)",
    )
    args = parser.parse_args()
    asyncio.run(main(skip_tier2=args.skip_tier2, verbose=args.verbose))
