"""Seed demo catalog for the SME customer demo (WP6 demo pack).

What it does: Ingests ~20 Vietnamese products (điện thoại / phụ kiện / laptop)
from ``scripts/product-catalog.json`` through the SAME unified RAG ingestion
pipeline the app uses (``ingest_product_text`` — LLM keyword extraction +
embedding + governance fields), then sets a non-zero ``stock_quantity`` per
SKU so the order/HITL demo scenario can actually decrement inventory.

Idempotent: ``ingest_product_text`` skips SKUs that already exist; stock is
re-applied on every run so a demo can be reset by simply re-running.

Usage:
    uv run python scripts/demo_seed.py               # seed 20 products
    uv run python scripts/demo_seed.py --limit 27    # seed the full catalog
    uv run python scripts/demo_seed.py --stock 50    # uniform stock level

Requires: Postgres (docker compose up -d db) + Ollama running (embeddings).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# ── stdlib path hack so script runs from repo root ──────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import update

from models.schema import Product
from services.database import AsyncSessionLocal
from services.rag.ingest import ingest_product_text

console = Console()
app = typer.Typer(name="demo-seed", help="Seed the demo product catalog.")

CATALOG_PATH = Path(__file__).parent / "product-catalog.json"


def _demo_stock(index: int, uniform: int | None) -> int:
    """Deterministic per-product stock so demo runs are reproducible."""
    if uniform is not None:
        return uniform
    return 8 + (index * 7) % 25  # 8..32, varied but stable per catalog order


async def _seed(limit: int, stock: int | None) -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["catalog"][:limit]

    table = Table(title=f"Demo seed — {len(catalog)} sản phẩm")
    table.add_column("SKU")
    table.add_column("Tên")
    table.add_column("Giá (VND)", justify="right")
    table.add_column("Tồn kho", justify="right")
    table.add_column("Trạng thái")

    ok = failed = 0
    async with AsyncSessionLocal() as db:
        for i, item in enumerate(catalog):
            sku = item["sku"]
            qty = _demo_stock(i, stock)
            try:
                await ingest_product_text(
                    db=db,
                    name=item["name"],
                    sku=sku,
                    description=item["description"],
                    price=float(item["price"]),
                    metadata={
                        "category": item.get("category"),
                        "subcategory": item.get("subcategory"),
                        "specifications": item.get("specifications", {}),
                        "intent": item.get("intent", "B2C"),
                    },
                )
                # ingest_product_text does not manage stock; demo needs stock > 0
                # so the order → HITL approve → decrement scenario works.
                await db.execute(
                    update(Product).where(Product.sku == sku).values(stock_quantity=qty)
                )
                await db.commit()
                ok += 1
                table.add_row(sku, item["name"][:40], f"{item['price']:,}", str(qty), "✅")
            except Exception as e:  # noqa: BLE001 — report per-product, keep seeding
                await db.rollback()
                failed += 1
                table.add_row(sku, item["name"][:40], f"{item['price']:,}", "-", f"❌ {e}")

    console.print(table)
    console.print(f"[bold]Done:[/bold] {ok} seeded, {failed} failed")
    if failed:
        raise typer.Exit(code=1)


@app.command()
def seed(
    limit: int = typer.Option(20, help="Number of products to seed from the catalog."),
    stock: int | None = typer.Option(
        None, help="Uniform stock quantity (default: deterministic 8-32 per product)."
    ),
) -> None:
    """Seed the demo catalog into the configured database."""
    asyncio.run(_seed(limit, stock))


if __name__ == "__main__":
    app()
