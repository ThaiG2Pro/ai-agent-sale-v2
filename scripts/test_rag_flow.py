"""RAG Flow Verification Test — local only.

Runs 3 queries against 1 ingested product and prints a step-by-step trace
of every stage: cache -> embed -> hybrid_search -> compress -> confidence ->
LLM -> cache_write -> result.

Usage:
    uv run python scripts/test_rag_flow.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# silence logfire token noise before importing anything that triggers it
os.environ.setdefault("LOGFIRE_IGNORE_NO_CONFIG", "1")

import logfire
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from services.database import AsyncSessionLocal
from services.rag.pipeline import answer_with_rag

logfire.configure(send_to_logfire=False)

console = Console()

PRODUCT_SKU = "PHONE-SM-001"
PRODUCT_NAME = "Samsung Galaxy S24 Ultra 256GB"

QUERIES = [
    {
        "label": "Q1 - Gia san pham (tieng Viet)",
        "text": "Giá của Samsung Galaxy S24 Ultra là bao nhiêu?",
        "expect": "price / giá",
    },
    {
        "label": "Q2 - Thong so ky thuat (tieng Viet)",
        "text": "Samsung Galaxy S24 Ultra có cấu hình như thế nào? RAM, chip, camera?",
        "expect": "specs / thông số",
    },
    {
        "label": "Q3 - Cache hit (same query as Q1)",
        "text": "Giá của Samsung Galaxy S24 Ultra là bao nhiêu?",
        "expect": "L1/L2 cache hit",
    },
]


def _section(title: str) -> None:
    console.print(f"\n[bold cyan]{'─' * 60}[/bold cyan]")
    console.print(f"[bold cyan]{title}[/bold cyan]")
    console.print(f"[bold cyan]{'─' * 60}[/bold cyan]")


def _ok(msg: str) -> None:
    console.print(f"  [green]✓[/green] {msg}")


def _warn(msg: str) -> None:
    console.print(f"  [yellow]⚠[/yellow] {msg}")


def _fail(msg: str) -> None:
    console.print(f"  [red]✗[/red] {msg}")


async def verify_db_state(db) -> bool:
    """Check 1 product + 1 embedding exist."""
    from sqlalchemy import text

    r = await db.execute(text("SELECT COUNT(*) FROM agent_v1.products"))
    n_products = r.scalar()
    r = await db.execute(text("SELECT COUNT(*) FROM agent_v1.text_embeddings"))
    n_embeddings = r.scalar()

    console.print(f"  Products  : [bold]{n_products}[/bold]")
    console.print(f"  Embeddings: [bold]{n_embeddings}[/bold]")

    if n_products == 0:
        _fail(
            "No products found -- run: "
            "uv run python scripts/ingest_catalog.py ingest --limit 1"
        )
        return False
    if n_embeddings == 0:
        _fail("No embeddings found — product ingested without embedding?")
        return False
    _ok("DB state OK")
    return True


async def run_rag_query(db, query_info: dict) -> dict:
    """Run single RAG query and return structured result."""
    _section(query_info["label"])
    console.print(f"  Query : [italic]{query_info['text']}[/italic]")
    console.print(f"  Expect: [dim]{query_info['expect']}[/dim]")

    t0 = time.perf_counter()
    result = await answer_with_rag(db, query_info["text"])
    elapsed = time.perf_counter() - t0

    # ── Print pipeline trace ──────────────────────────────────────────────────
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("key", style="dim", width=28)
    t.add_column("value", style="bold")

    t.add_row("model_used", result.model_used)
    t.add_row("query_category", result.query_category)
    t.add_row("top_k_used", str(result.top_k_used))
    t.add_row("best_similarity", f"{result.best_similarity:.4f}")
    t.add_row("similarity_gap", f"{result.similarity_gap:.4f}")
    t.add_row("chunks_before_compression", str(result.chunks_before_compression))
    t.add_row("chunks_after_compression", str(result.chunks_after_compression))
    t.add_row("declined", str(result.declined))
    t.add_row("escalation_flag", str(result.escalation_flag))
    t.add_row("citations", str(len(result.citations)))
    t.add_row("latency_s", f"{elapsed:.2f}s")
    console.print(t)

    # ── Answer ────────────────────────────────────────────────────────────────
    color = "red" if result.declined else "green"
    console.print(
        Panel(
            result.answer[:400] + ("…" if len(result.answer) > 400 else ""),
            title="[bold]Answer[/bold]",
            border_style=color,
            expand=False,
        )
    )

    # ── Checks ────────────────────────────────────────────────────────────────
    issues = []
    if result.declined:
        issues.append("DECLINED — confidence guard fired (best_similarity too low?)")
    if result.best_similarity < 0.35 and not result.declined:
        issues.append(f"Low similarity {result.best_similarity:.3f} but not declined")
    if result.model_used == "cache" and query_info["label"].startswith("Q1"):
        issues.append("Unexpected cache hit on first query (cache was empty?)")
    if result.model_used != "cache" and "cache hit" in query_info["expect"]:
        issues.append(f"Expected cache hit but got model_used={result.model_used}")
    if not result.citations and not result.declined:
        issues.append("No citations returned despite non-declined answer")

    if issues:
        for issue in issues:
            _warn(issue)
    else:
        _ok("All checks passed")

    return {
        "label": query_info["label"],
        "elapsed": elapsed,
        "declined": result.declined,
        "model_used": result.model_used,
        "best_similarity": result.best_similarity,
        "chunks_returned": result.chunks_after_compression,
        "citations": len(result.citations),
        "issues": issues,
    }


async def main() -> None:
    console.print(
        Panel.fit(
            "[bold white]RAG Flow Verification — Local Only[/bold white]\n"
            f"Product: {PRODUCT_NAME}",
            border_style="blue",
        )
    )

    async with AsyncSessionLocal() as db:
        # ── Step 0: DB State ──────────────────────────────────────────────────
        _section("Step 0 — DB State")
        ok = await verify_db_state(db)
        if not ok:
            sys.exit(1)

        # ── Steps 1-3: Run queries ────────────────────────────────────────────
        results = []
        for q in QUERIES:
            r = await run_rag_query(db, q)
            results.append(r)
            # commit so cache writes from Q1 are visible to Q3
            await db.commit()

        # ── Summary ───────────────────────────────────────────────────────────
        _section("Summary")
        summary = Table(title="RAG Flow Test Results", box=box.ROUNDED)
        summary.add_column("Query", style="cyan", width=35)
        summary.add_column("Model", width=12)
        summary.add_column("Similarity", justify="right", width=10)
        summary.add_column("Chunks", justify="right", width=8)
        summary.add_column("Latency", justify="right", width=10)
        summary.add_column("Status", width=12)

        all_pass = True
        for r in results:
            status = "[red]FAIL[/red]" if r["issues"] else "[green]PASS[/green]"
            if r["issues"]:
                all_pass = False
            summary.add_row(
                r["label"][:35],
                r["model_used"],
                f"{r['best_similarity']:.3f}",
                str(r["chunks_returned"]),
                f"{r['elapsed']:.1f}s",
                status,
            )
        console.print(summary)

        if all_pass:
            console.print("\n[bold green]✓ RAG flow working correctly[/bold green]")
        else:
            console.print(
                "\n[bold yellow]Some checks failed - review issues above[/bold yellow]"
            )
            all_issues = [i for r in results for i in r["issues"]]
            for issue in all_issues:
                console.print(f"  [red]•[/red] {issue}")


if __name__ == "__main__":
    asyncio.run(main())
