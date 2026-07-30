"""Why this exists: WP-V2-0 — an eval gate that is cheap enough to actually run.
What it does: Tiered evaluation over tests/eval/gold_dataset.json with 100%
deterministic grading (no LLM-as-judge anywhere):

  Tier-R (default): retrieval-only recall@k. Embeds the RAW query (bypasses the
          LLM normalize step on purpose) + hybrid RRF search, then checks that
          expected_skus appear in the top-k. Cost: embed calls only — zero chat
          LLM calls, so it never touches chat-model VRAM or rate limits.
  Tier-F: full-pipeline smoke over the small `tier_f: true` subset (answer
          generation needed to grade must_decline / expected_price /
          absent_terms hallucination traps). Sequential + backoff on rate
          limits, JSONL checkpoint so a crash/429 mid-run never loses results.

Results are compared against the latest committed baseline in
tests/eval/baselines/ — exit code 1 when the pass rate regresses by more than
--threshold percentage points (default 2.0).

Usage:
  uv run python scripts/eval_gate.py --tier r                # default gate
  uv run python scripts/eval_gate.py --tier f                # smoke subset
  uv run python scripts/eval_gate.py --tier all              # everything (manual/pre-release)
  uv run python scripts/eval_gate.py --tier r --save-baseline
  uv run python scripts/eval_gate.py --tier f --category hallucination_trap --limit 2
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GOLD_DATASET_PATH = Path("tests/eval/gold_dataset.json")
BASELINE_DIR = Path("tests/eval/baselines")
RUNS_DIR = Path("reports/eval_runs")

DEFAULT_TOP_K = 10
DEFAULT_THRESHOLD_PP = 2.0
BACKOFF_BASE_SECONDS = 5.0
MAX_ATTEMPTS = 3

_PRICE_NEARBY_RE = re.compile(
    r"\d{5,}\s*(?:₫|đ\b|vnd|vnđ|dong|đồng)|\d+\s*(?:trieu|triệu|tr\b)|\d{7,}",
    re.IGNORECASE,
)
_DIGIT_GROUP_RE = re.compile(r"(?<=\d)[.,\s](?=\d)")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n•;]+")


# ── Pure grading helpers (unit-tested, no I/O) ──────────────────────────────


def normalize_digits(text: str) -> str:
    """Collapse digit-group separators so '24.990.000₫' and '24,990,000 VND' both
    contain the substring '24990000'."""
    return _DIGIT_GROUP_RE.sub("", text)


def grade_tier_r(expected_skus: list[str], found_skus: list[str], match: str = "all") -> dict:
    """recall@k grading: did the expected SKUs make it into the top-k?"""
    found_set = {s.upper() for s in found_skus}
    hits = [s for s in expected_skus if s.upper() in found_set]
    recall = len(hits) / len(expected_skus) if expected_skus else 1.0
    passed = bool(hits) if match == "any" else len(hits) == len(expected_skus)
    return {"recall": round(recall, 4), "hits": hits, "passed": passed}


def _states_price_near(answer: str, term: str) -> bool:
    """True when any sentence mentions `term` AND states a price-looking figure —
    the deterministic signature of a fabricated claim about an absent product.
    Digits are normalized FIRST so '27.990.000₫' survives sentence splitting."""
    for sentence in _SENTENCE_SPLIT_RE.split(normalize_digits(answer.lower())):
        if term.lower() in sentence and _PRICE_NEARBY_RE.search(sentence):
            return True
    return False


def grade_tier_f(case: dict, answer: str, declined: bool, citations: list) -> dict:
    """Deterministic full-pipeline grading. Rules (all that apply must pass):
    - must_decline: declined must be True.
    - expected_price: digit-normalized answer must contain the digit-normalized
      price (declined answers fail — the price was askable).
    - absent_terms (hallucination trap): pass when declined, OR when no sentence
      states a price next to the absent product name (polite redirects pass).
    - otherwise: must not decline and must return >=1 citation.
    """
    checks: dict[str, bool] = {}
    if case.get("must_decline"):
        checks["must_decline"] = declined
    if case.get("expected_price") is not None:
        checks["expected_price"] = not declined and str(
            case["expected_price"]
        ) in normalize_digits(answer)
    if case.get("absent_terms"):
        checks["no_hallucinated_price"] = declined or not any(
            _states_price_near(answer, t) for t in case["absent_terms"]
        )
    if not checks:  # plain answerable case
        checks["answered_with_citations"] = not declined and len(citations) > 0
    return {"checks": checks, "passed": all(checks.values())}


def dataset_hash(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()[:12]


def load_completed(jsonl_path: Path, ds_hash: str) -> dict[str, dict]:
    """Resume support: previously completed case results for this exact dataset."""
    completed: dict[str, dict] = {}
    if not jsonl_path.exists():
        return completed
    for line in jsonl_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("dataset_hash") == ds_hash and "passed" in rec:
            completed[rec["id"]] = rec
    return completed


def append_result(jsonl_path: Path, rec: dict) -> None:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def summarize(results: list[dict]) -> dict:
    by_cat: dict[str, dict] = {}
    for r in results:
        cat = by_cat.setdefault(r.get("category", "?"), {"total": 0, "passed": 0})
        cat["total"] += 1
        cat["passed"] += 1 if r.get("passed") else 0
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    return {
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4) if total else None,
        "by_category": {
            k: {**v, "pass_rate": round(v["passed"] / v["total"], 4)}
            for k, v in sorted(by_cat.items())
        },
    }


def latest_baseline(baseline_dir: Path, tier: str) -> dict | None:
    files = sorted(baseline_dir.glob(f"tier-{tier}-*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text())


def compare_to_baseline(current: dict, baseline: dict, threshold_pp: float) -> dict:
    cur, base = current.get("pass_rate"), baseline.get("summary", {}).get("pass_rate")
    if cur is None or base is None:
        return {"regressed": False, "delta_pp": None}
    delta_pp = round((cur - base) * 100, 2)
    return {
        "regressed": delta_pp < -threshold_pp,
        "delta_pp": delta_pp,
        "baseline_pass_rate": base,
    }


def is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        t in msg
        for t in ("429", "rate limit", "ratelimit", "overloaded", "timeout", "temporarily")
    )


# ── Runners (I/O — imported lazily so unit tests never touch the app stack) ──


async def _with_backoff(coro_factory, case_id: str):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            if attempt == MAX_ATTEMPTS or not is_retryable(exc):
                raise
            wait = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            print(
                f"  [{case_id}] retryable error ({exc}); backing off {wait:.0f}s "
                f"(attempt {attempt}/{MAX_ATTEMPTS})"
            )
            await asyncio.sleep(wait)


async def run_tier_r(cases: list[dict], ds_hash: str, resume: dict[str, dict]) -> list[dict]:
    from services.ai import AIGateway
    from services.database import AsyncSessionLocal
    from services.rag.retrieval import hybrid_search_rrf

    pending = [c for c in cases if c["id"] not in resume]
    results = list(resume.values())
    if not pending:
        return results

    # ONE batched embed call for all pending raw queries — the whole point of
    # Tier-R is that this is the only model call in the gate.
    queries = [c["query"] for c in pending]
    vectors = await AIGateway.embed(queries)

    jsonl = RUNS_DIR / "tier-r.jsonl"
    async with AsyncSessionLocal() as db:
        for case, vec in zip(pending, vectors, strict=True):
            top_k = int(case.get("top_k", DEFAULT_TOP_K))
            rows = await hybrid_search_rrf(db, vec, case["query"], top_k)
            found = [r["sku"] for r in rows[:top_k]]
            grade = grade_tier_r(case.get("expected_skus", []), found, case.get("match", "all"))
            rec = {
                "id": case["id"],
                "tier": "r",
                "category": case.get("category"),
                "dataset_hash": ds_hash,
                "top_k": top_k,
                "found_skus": found,
                **grade,
            }
            append_result(jsonl, rec)
            results.append(rec)
            mark = "✓" if grade["passed"] else "✗"
            print(f"  {mark} [{case['id']}] recall={grade['recall']:.2f} {case['query'][:60]}")
    return results


async def run_tier_f(cases: list[dict], ds_hash: str, resume: dict[str, dict]) -> list[dict]:
    from services.database import AsyncSessionLocal
    from services.rag import answer_with_rag

    jsonl = RUNS_DIR / "tier-f.jsonl"
    results = list(resume.values())
    async with AsyncSessionLocal() as db:
        for case in cases:
            if case["id"] in resume:
                continue
            try:
                rag = await _with_backoff(
                    lambda q=case["query"]: answer_with_rag(db, q), case["id"]
                )
            except Exception as exc:
                rec = {
                    "id": case["id"],
                    "tier": "f",
                    "category": case.get("category"),
                    "dataset_hash": ds_hash,
                    "passed": False,
                    "pipeline_error": str(exc)[:300],
                }
                append_result(jsonl, rec)
                results.append(rec)
                print(f"  ✗ [{case['id']}] pipeline error: {str(exc)[:120]}")
                continue
            grade = grade_tier_f(case, rag.answer, rag.declined, rag.citations)
            rec = {
                "id": case["id"],
                "tier": "f",
                "category": case.get("category"),
                "dataset_hash": ds_hash,
                "declined": rag.declined,
                "answer_snippet": rag.answer[:200],
                "citation_count": len(rag.citations),
                **grade,
            }
            append_result(jsonl, rec)
            results.append(rec)
            mark = "✓" if grade["passed"] else "✗"
            print(f"  {mark} [{case['id']}] {grade['checks']} {case['query'][:50]}")
    return results


async def _flush_semantic_cache() -> None:
    """--flush-cache: empty semantic_cache so Tier-F answers are generated fresh.

    A previous run caches its answers (L1/L2); without this, a behavior change in
    the generation path is invisible — the gate replays yesterday's answers
    (discovered in WP-V2-1: 4 stale out_of_catalog answers masked the new
    groundedness decline).
    """
    from sqlalchemy import text as sql_text

    from services.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await db.execute(sql_text("TRUNCATE agent_v1.semantic_cache"))
        await db.commit()
    print("semantic_cache flushed (--flush-cache).")


def main() -> int:
    parser = argparse.ArgumentParser(description="Tiered deterministic eval gate (WP-V2-0)")
    parser.add_argument("--tier", choices=["r", "f", "all"], default="r")
    parser.add_argument("--category", help="only run cases in this category")
    parser.add_argument("--limit", type=int, help="max cases per tier")
    parser.add_argument(
        "--rerun", action="store_true", help="ignore JSONL checkpoints, run all again"
    )
    parser.add_argument(
        "--flush-cache",
        action="store_true",
        help="truncate semantic_cache before Tier-F so answers cached by a previous "
        "run (pre-change behavior) cannot mask the effect of the change under test",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="write result as new baseline instead of comparing",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD_PP,
        help="max allowed pass-rate drop in percentage points",
    )
    args = parser.parse_args()

    raw = GOLD_DATASET_PATH.read_bytes()
    ds_hash = dataset_hash(raw)
    dataset: list[dict] = json.loads(raw)
    if args.category:
        dataset = [c for c in dataset if c.get("category") == args.category]

    exit_code = 0
    for tier in ["r", "f"] if args.tier == "all" else [args.tier]:
        if tier == "r":
            cases = [c for c in dataset if c.get("expected_skus")]
        else:
            cases = [c for c in dataset if c.get("tier_f")] if args.tier != "all" else dataset
        if args.limit:
            cases = cases[: args.limit]
        if not cases:
            print(f"Tier-{tier.upper()}: no matching cases — skipped.")
            continue

        jsonl = RUNS_DIR / f"tier-{tier}.jsonl"
        if args.rerun and jsonl.exists():
            jsonl.unlink()
        resume = {} if args.rerun else load_completed(jsonl, ds_hash)
        resume = {k: v for k, v in resume.items() if k in {c["id"] for c in cases}}
        if resume:
            print(f"Tier-{tier.upper()}: resuming — {len(resume)} case(s) already done.")

        print(f"\n═══ Tier-{tier.upper()} — {len(cases)} case(s), dataset {ds_hash} ═══")
        runner = run_tier_r if tier == "r" else run_tier_f
        try:
            if args.flush_cache and tier == "f":
                asyncio.run(_flush_semantic_cache())
            results = asyncio.run(runner(cases, ds_hash, resume))
        except Exception as exc:  # litellm APIConnectionError, asyncpg errors, …
            print(
                f"\n🔴 Tier-{tier.upper()} aborted — {type(exc).__name__}: {str(exc)[:200]}\n"
                "   Checklist: docker compose up -d db · Ollama running "
                "(embed model bge-m3) · products seeded (scripts/demo_seed.py).\n"
                "   Completed cases were checkpointed; re-running resumes where this stopped."
            )
            return 2
        summary = summarize(results)
        print(
            f"\nTier-{tier.upper()} summary: {summary['passed']}/{summary['total']} "
            f"({(summary['pass_rate'] or 0):.0%})"
        )
        for cat, s in summary["by_category"].items():
            print(f"  {cat:20s} {s['passed']}/{s['total']}")

        payload = {
            "tier": tier,
            "dataset_hash": ds_hash,
            "created_at": datetime.now(UTC).isoformat(),
            "summary": summary,
            "results": sorted(results, key=lambda r: r["id"]),
        }
        if args.save_baseline:
            BASELINE_DIR.mkdir(parents=True, exist_ok=True)
            out = BASELINE_DIR / f"tier-{tier}-{datetime.now(UTC).strftime('%Y%m%d')}.json"
            out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
            print(f"Baseline saved → {out}")
        else:
            baseline = latest_baseline(BASELINE_DIR, tier)
            if baseline is None:
                print("No baseline yet — run with --save-baseline to create one. (gate: PASS)")
            else:
                cmp_ = compare_to_baseline(summary, baseline, args.threshold)
                if cmp_["regressed"]:
                    print(
                        f"🔴 GATE FAIL: pass rate {cmp_['delta_pp']}pp vs baseline "
                        f"({cmp_['baseline_pass_rate']:.0%}), threshold -{args.threshold}pp"
                    )
                    exit_code = 1
                else:
                    print(f"🟢 GATE PASS (Δ {cmp_['delta_pp']}pp vs baseline)")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
