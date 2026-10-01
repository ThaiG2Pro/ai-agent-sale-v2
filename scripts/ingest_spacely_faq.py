"""Ingest the Spacely FAQ / pricing knowledge as the agent's RAG corpus.

Why this exists (2026-10-01): Spacely's support widget uses this agent with
``AGENT_PERSONA=spacely_support``. The graph's knowledge base is the
``products`` table, so each FAQ entry becomes one "product" (price 0, sku
``spacely-<id>``) and rides the unchanged ingest pipeline (embedding +
keyword extraction → hybrid search).

Source of truth stays in the Spacely repo (``src/content/faq.ts`` + credit
packages); the Next.js app serves it as JSON at
``GET /api/v1/support/knowledge`` so the two never drift. Pass ``--file`` to
ingest a saved copy of that JSON instead.

Re-runnable: every ``spacely-*`` product is deleted first (embeddings cascade),
then re-ingested, so edited answers replace stale ones. Run it against the
Spacely agent DB only (see scripts/run_spacely_local.sh) — never the shop DB.

Usage:
    uv run python scripts/ingest_spacely_faq.py --url http://localhost:3000/api/v1/support/knowledge
    uv run python scripts/ingest_spacely_faq.py --file /tmp/knowledge.json
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import delete, select

from models.schema import Product
from services.database import AsyncSessionLocal
from services.rag.ingest import ingest_product_text
from services.semantic_cache import invalidate_cache

console = Console()
app = typer.Typer(name="ingest-spacely-faq", help="(Re)ingest Spacely FAQ knowledge.")

SKU_PREFIX = "spacely-"


def _load(url: str | None, file: Path | None) -> dict:
    if file:
        return json.loads(file.read_text(encoding="utf-8"))
    if not url:
        raise typer.BadParameter("pass --url or --file")
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def _run(payload: dict) -> None:
    docs = payload.get("docs") or []
    if not docs:
        console.print("[red]No docs in payload[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Spacely knowledge — {len(docs)} docs")
    table.add_column("SKU")
    table.add_column("Chủ đề")
    table.add_column("Tiêu đề")
    table.add_column("Trạng thái")

    ok = failed = 0
    async with AsyncSessionLocal() as db:
        # Purge previous corpus so edited/removed FAQ entries do not linger.
        stale = (
            (await db.execute(select(Product.sku).where(Product.sku.like(f"{SKU_PREFIX}%"))))
            .scalars()
            .all()
        )
        if stale:
            await db.execute(delete(Product).where(Product.sku.like(f"{SKU_PREFIX}%")))
            await db.commit()
            console.print(f"🧹 removed {len(stale)} previous spacely-* docs")

        for doc in docs:
            sku = f"{SKU_PREFIX}{doc['id']}"[:50]
            try:
                await ingest_product_text(
                    db=db,
                    name=doc["title"],
                    sku=sku,
                    description=doc["body"],
                    price=0.0,
                    metadata={
                        "category": doc.get("topicLabel") or doc.get("topic"),
                        "subcategory": doc.get("topic"),
                        "keywords": doc.get("keywords") or [],
                        "intent": "SUPPORT",
                        "source": "spacely-knowledge",
                    },
                )
                ok += 1
                table.add_row(sku, str(doc.get("topic", "")), doc["title"][:48], "✅")
            except Exception as exc:  # keep going — one bad doc must not block the rest
                failed += 1
                await db.rollback()
                table.add_row(sku, str(doc.get("topic", "")), doc["title"][:48], f"❌ {exc}")

        await invalidate_cache(db)

    console.print(table)
    console.print(f"Done: {ok} ingested, {failed} failed")
    if failed:
        raise typer.Exit(1)


@app.command()
def main(
    url: str | None = typer.Option(None, help="Spacely knowledge endpoint"),
    file: Path | None = typer.Option(None, exists=True, help="Saved knowledge JSON"),
) -> None:
    payload = _load(url, file)
    asyncio.run(_run(payload))


if __name__ == "__main__":
    app()
