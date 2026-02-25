"""Why this exists: Performs Tier 1 (Heuristic) and Tier 2 (HITL) evaluation.
What it does: Runs queries from gold_dataset.json and collects human grades.
"""

from __future__ import annotations

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
from services.rag import search_products

GOLD_DATASET_PATH = "tests/eval/gold_dataset.json"
RESULTS_PATH = "reports/eval_results.json"


async def evaluate_query(db: Any, item: dict[str, Any]) -> dict[str, Any]:
    """Runs a single evaluation item."""
    query = item["query"]
    expected_keywords = item.get("expected_keywords", [])

    print(f"\n--- [ID: {item['id']}] ---")
    print(f"Query: {query}")

    # 1. Run Retrieval
    try:
        results = await search_products(db, query, top_k=3)
    except Exception as e:
        print(f"Retrieval failed: {e}")
        return {"id": item["id"], "error": str(e)}

    # 2. Automated Tier 1 Check: Keyword Presence in top result description
    found_keywords = []
    if results:
        desc = results[0]["description"]
        top_text = desc.lower() if desc else ""
        for kw in expected_keywords:
            if kw.lower() in top_text:
                found_keywords.append(kw)

    keyword_score = (
        len(found_keywords) / len(expected_keywords) if expected_keywords else 1.0
    )
    print(f"Tier 1 (Keywords): {len(found_keywords)}/{len(expected_keywords)} matched.")

    # 3. Tier 2 (HITL) Grading
    print("\nTop Result:")
    if results:
        print(f" - [{results[0]['sku']}] {results[0]['name']}")
        print(f"   Score: {results[0]['score']:.4f}")
    else:
        print(" - No results found.")

    while True:
        try:
            prompt = "\nRate quality (1-5) or 's' to skip: "
            # Use anyio.to_thread to avoid ASYNC250
            grade_input = await anyio.to_thread.run_sync(input, prompt)
            grade_input = grade_input.strip().lower()
            if grade_input == "s":
                human_grade = None
                break
            grade = int(grade_input)
            if 1 <= grade <= 5:
                human_grade = grade
                break
            else:
                print("Please enter a number between 1 and 5.")
        except ValueError:
            print("Invalid input. Enter a number 1-5.")

    return {
        "id": item["id"],
        "query": query,
        "keyword_score": keyword_score,
        "human_grade": human_grade,
        "top_sku": results[0]["sku"] if results else None,
        "results_count": len(results),
    }


async def main():
    setup_logging()
    gold_path = Path(GOLD_DATASET_PATH)
    if not await gold_path.exists():
        print(f"Error: Gold dataset not found at {GOLD_DATASET_PATH}")
        sys.exit(1)

    # Async read
    content = await gold_path.read_text()
    dataset = json.loads(content)

    os.makedirs("reports", exist_ok=True)

    results = []
    async with AsyncSessionLocal() as db:
        for item in dataset:
            result = await evaluate_query(db, item)
            results.append(result)

    # Calculate summary
    valid_human_grades = [
        r["human_grade"] for r in results if r.get("human_grade") is not None
    ]
    avg_human = (
        sum(valid_human_grades) / len(valid_human_grades) if valid_human_grades else 0
    )
    avg_keyword = (
        sum(r["keyword_score"] for r in results if "keyword_score" in r) / len(results)
        if results
        else 0
    )

    summary = {
        "total_cases": len(results),
        "avg_keyword_score": avg_keyword,
        "avg_human_grade": avg_human,
        "details": results,
    }

    # Async write
    results_path = Path(RESULTS_PATH)
    await results_path.write_text(json.dumps(summary, indent=2))

    print("\n" + "=" * 30)
    print("EVALUATION COMPLETE")
    print(f"Total Cases: {len(results)}")
    print(f"Avg Keyword Score: {avg_keyword:.2%}")
    print(f"Avg Human Grade: {avg_human:.2f}/5")
    print(f"Results saved to: {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
