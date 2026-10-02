"""Eval gate for the Spacely support graph (docs/upgrade-plan-support-graph.md, bước 6).

Same shape as scripts/eval_gate.py (which stays the shop gate): deterministic
grading, JSONL checkpoints, committed baselines, regression threshold. Reuses
its pure helpers; only the dataset, the graph and the grading rules differ.

  Tier-R  retrieval recall of expected_skus (spacely-*) — embed calls only.
  Tier-F  every case through build_support_graph (fresh MemorySaver per case):
          expected_intent · must_decline (decline or polite redirect) ·
          expected_skus cited · required_any
          (any phrase present) · forbidden_terms (none present) · plain cases
          must answer with ≥1 citation.

Run against the Spacely DB only:
  ./scripts/run_spacely_local.sh eval --tier all --save-baseline   # first time
  ./scripts/run_spacely_local.sh eval --tier r                     # cheap gate
  ./scripts/run_spacely_local.sh eval --tier f --flush-cache
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.eval_gate import (
    DEFAULT_THRESHOLD_PP,
    DEFAULT_TOP_K,
    _with_backoff,
    append_result,
    compare_to_baseline,
    dataset_hash,
    grade_tier_r,
    latest_baseline,
    load_completed,
    summarize,
)

GOLD_DATASET_PATH = Path("tests/eval/support_gold.json")
BASELINE_DIR = Path("tests/eval/baselines")
RUNS_DIR = Path("reports/eval_runs")
TIER_PREFIX = "support"  # baselines: tier-support-r-*.json / tier-support-f-*.json


# ── Pure grading (unit-tested) ──────────────────────────────────────────────

REDIRECT_PHRASES = (
    "chưa có thông tin",
    "không có thông tin",
    "chỉ hỗ trợ",
    "không hỗ trợ",
    "không thể trả lời",
    "ngoài phạm vi",
    "không bán",
    "không phải là",
    "không liên quan",
)


def grade_support_f(
    case: dict, answer: str, declined: bool, intent: str, cited_skus: list[str]
) -> dict:
    """All applicable checks must pass."""
    low = answer.lower()
    checks: dict[str, bool] = {}
    if case.get("expected_intent"):
        checks["intent"] = intent == case["expected_intent"]
    if case.get("must_decline"):
        # Out-of-scope: a confidence decline OR a polite in-scope redirect
        # ("mình chỉ hỗ trợ Spacely…") both pass; forbidden_terms on the case
        # catch the failure mode that matters (answering the off-topic ask).
        checks["declined_or_redirect"] = declined or any(p in low for p in REDIRECT_PHRASES)
    if case.get("expected_skus"):
        want = set(case["expected_skus"])
        checks["cited_expected"] = not declined and bool(want & set(cited_skus))
    if case.get("required_any"):
        checks["required_any"] = any(t.lower() in low for t in case["required_any"])
    if case.get("forbidden_terms"):
        checks["no_forbidden"] = not any(t.lower() in low for t in case["forbidden_terms"])
    if not checks:
        checks["answered_with_citations"] = not declined and len(cited_skus) > 0
    return {"checks": checks, "passed": all(checks.values())}


# ── Runners ─────────────────────────────────────────────────────────────────


async def run_tier_r(
    cases: list[dict], ds_hash: str, resume: dict[str, dict], sleep_s: float = 0.0
) -> list[dict]:
    from services.ai import AIGateway
    from services.database import AsyncSessionLocal
    from services.rag.retrieval import hybrid_search_rrf

    pending = [c for c in cases if c["id"] not in resume]
    results = list(resume.values())
    if not pending:
        return results
    vectors = await AIGateway.embed([c["query"] for c in pending])
    jsonl = RUNS_DIR / f"tier-{TIER_PREFIX}-r.jsonl"
    async with AsyncSessionLocal() as db:
        for case, vec in zip(pending, vectors, strict=True):
            top_k = int(case.get("top_k", DEFAULT_TOP_K))
            rows = await hybrid_search_rrf(db, vec, case["query"], top_k)
            found = [r["sku"] for r in rows[:top_k]]
            grade = grade_tier_r(case["expected_skus"], found, case.get("match", "all"))
            rec = {
                "id": case["id"],
                "tier": f"{TIER_PREFIX}-r",
                "category": case.get("category"),
                "dataset_hash": ds_hash,
                "top_k": top_k,
                "found_skus": found,
                **grade,
            }
            append_result(jsonl, rec)
            results.append(rec)
            print(
                f"  {'✓' if grade['passed'] else '✗'} [{case['id']}] recall={grade['recall']:.2f} {case['query'][:60]}"
            )
    return results


async def _answer_via_support_graph(db, query: str) -> dict:
    import uuid

    from langgraph.checkpoint.memory import MemorySaver

    from core.agent.graph import make_agent_config
    from core.agent.state import make_initial_state
    from core.support.graph import build_support_graph

    session = f"eval-support-{uuid.uuid4().hex[:12]}"
    graph = build_support_graph(checkpointer=MemorySaver())
    state = make_initial_state(query, session_id=session, customer_id="eval-support")
    final = await graph.ainvoke(state, config=make_agent_config(session, db=db))
    turn_ids = {c.get("product_id") for c in (final.get("retrieved_chunks") or [])}
    cited = []
    for c in final.get("citations") or []:
        pid = getattr(c, "product_id", None) or (
            c.get("product_id") if isinstance(c, dict) else None
        )
        sku = getattr(c, "sku", None) or (c.get("sku") if isinstance(c, dict) else None)
        if sku and (not turn_ids or pid in turn_ids):
            cited.append(sku)
    return {
        "answer": final.get("response") or "",
        "declined": bool(final.get("declined")),
        "intent": final.get("intent") or "UNKNOWN",
        "cited_skus": cited,
    }


async def run_tier_f(
    cases: list[dict], ds_hash: str, resume: dict[str, dict], sleep_s: float = 0.0
) -> list[dict]:
    from services.database import AsyncSessionLocal

    jsonl = RUNS_DIR / f"tier-{TIER_PREFIX}-f.jsonl"
    results = list(resume.values())
    async with AsyncSessionLocal() as db:
        for case in cases:
            if case["id"] in resume:
                continue
            if sleep_s and results:
                await asyncio.sleep(sleep_s)  # Groq free tier: 8k TPM on the chat model
            try:
                out = await _with_backoff(
                    lambda q=case["query"]: _answer_via_support_graph(db, q), case["id"]
                )
            except Exception as exc:
                rec = {
                    "id": case["id"],
                    "tier": f"{TIER_PREFIX}-f",
                    "category": case.get("category"),
                    "dataset_hash": ds_hash,
                    "passed": False,
                    "pipeline_error": str(exc)[:300],
                }
                append_result(jsonl, rec)
                results.append(rec)
                print(f"  ✗ [{case['id']}] pipeline error: {str(exc)[:120]}")
                continue
            grade = grade_support_f(
                case, out["answer"], out["declined"], out["intent"], out["cited_skus"]
            )
            rec = {
                "id": case["id"],
                "tier": f"{TIER_PREFIX}-f",
                "category": case.get("category"),
                "dataset_hash": ds_hash,
                "intent": out["intent"],
                "declined": out["declined"],
                "cited_skus": out["cited_skus"],
                "answer_snippet": out["answer"][:200],
                **grade,
            }
            append_result(jsonl, rec)
            results.append(rec)
            print(
                f"  {'✓' if grade['passed'] else '✗'} [{case['id']}] {out['intent']} {grade['checks']} {case['query'][:40]}"
            )
    return results


async def _flush_semantic_cache() -> None:
    from sqlalchemy import text as sql_text

    from services.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await db.execute(sql_text("TRUNCATE agent_v1.semantic_cache"))
        await db.commit()
    print("semantic_cache flushed (--flush-cache).")


async def _run_tier(
    tier: str, cases: list[dict], ds_hash: str, resume: dict, flush: bool, sleep_s: float
) -> list[dict]:
    if flush and tier == "f":
        await _flush_semantic_cache()
    runner = run_tier_r if tier == "r" else run_tier_f
    results = await runner(cases, ds_hash, resume, sleep_s)
    from services.database import engine

    await engine.dispose()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Spacely support graph eval gate")
    parser.add_argument("--tier", choices=["r", "f", "all"], default="r")
    parser.add_argument("--category")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--rerun", action="store_true")
    parser.add_argument("--flush-cache", action="store_true")
    parser.add_argument("--save-baseline", action="store_true")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_PP)
    parser.add_argument("--sleep", type=float, default=4.0, help="seconds between Tier-F cases")
    args = parser.parse_args()

    raw = GOLD_DATASET_PATH.read_bytes()
    ds_hash = dataset_hash(raw)
    dataset: list[dict] = json.loads(raw)
    if args.category:
        dataset = [c for c in dataset if c.get("category") == args.category]

    exit_code = 0
    for tier in ["r", "f"] if args.tier == "all" else [args.tier]:
        cases = [c for c in dataset if c.get("expected_skus")] if tier == "r" else dataset
        if args.limit:
            cases = cases[: args.limit]
        tier_name = f"{TIER_PREFIX}-{tier}"
        jsonl = RUNS_DIR / f"tier-{tier_name}.jsonl"
        if args.rerun and jsonl.exists():
            jsonl.unlink()
        resume = {} if args.rerun else load_completed(jsonl, ds_hash)
        resume = {k: v for k, v in resume.items() if k in {c["id"] for c in cases}}
        if resume:
            print(f"Tier-{tier.upper()}: resuming — {len(resume)} case(s) already done.")
        print(f"\n═══ Support Tier-{tier.upper()} — {len(cases)} case(s), dataset {ds_hash} ═══")
        try:
            # One event loop per tier (flush + run + dispose): the async engine
            # binds to the first loop it is used on; a second asyncio.run()
            # otherwise fails with "attached to a different loop".
            results = asyncio.run(
                _run_tier(tier, cases, ds_hash, resume, args.flush_cache, args.sleep)
            )
        except Exception as exc:
            print(f"\n🔴 aborted — {type(exc).__name__}: {str(exc)[:200]}")
            return 2
        summary = summarize(results)
        print(
            f"\nSupport Tier-{tier.upper()}: {summary['passed']}/{summary['total']} ({(summary['pass_rate'] or 0):.0%})"
        )
        for cat, s in summary["by_category"].items():
            print(f"  {cat:16s} {s['passed']}/{s['total']}")
        payload = {
            "tier": tier_name,
            "dataset_hash": ds_hash,
            "created_at": datetime.now(UTC).isoformat(),
            "summary": summary,
            "results": sorted(results, key=lambda r: r["id"]),
        }
        if args.save_baseline:
            BASELINE_DIR.mkdir(parents=True, exist_ok=True)
            out = BASELINE_DIR / f"tier-{tier_name}-{datetime.now(UTC).strftime('%Y%m%d')}.json"
            out.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
            print(f"Baseline saved → {out}")
        else:
            baseline = latest_baseline(BASELINE_DIR, tier_name)
            if baseline is None:
                print("No baseline yet — run with --save-baseline. (gate: PASS)")
            else:
                cmp_ = compare_to_baseline(summary, baseline, args.threshold)
                if cmp_["regressed"]:
                    print(
                        f"🔴 GATE FAIL: {cmp_['delta_pp']}pp vs baseline ({cmp_['baseline_pass_rate']:.0%})"
                    )
                    exit_code = 1
                else:
                    print(f"🟢 GATE PASS (Δ {cmp_['delta_pp']}pp vs baseline)")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
