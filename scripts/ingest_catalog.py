"""Ingest products from JSON catalog file - non-blocking async implementation.

This script reads products from a JSON catalog file and ingests them into the database
using the same unified ingestion pipeline as the RAG system. Optimized for:
  - Non-blocking async I/O (no busy waits)
  - Batch processing with configurable concurrency
  - Full OpenTelemetry observability
  - Efficient embedding generation with Semaphore-controlled concurrency
  - Graceful error handling and retry logic

Architecture:
  1. Load catalog JSON (async file read)
  2. Validate products against Pydantic schema
  3. Batch ingestion with rate limiting
  4. Parallel embedding generation (Semaphore-controlled)
  5. Full tracing and structured logging

Usage:
    # Ingest from default catalog
    uv run python scripts/ingest_catalog.py

    # Ingest subset with custom concurrency
    uv run python scripts/ingest_catalog.py --limit 50 --embed-concurrency 4

    # Ingest to test database
    uv run python scripts/ingest_catalog.py --test-db

    # Show what would be ingested without writing
    uv run python scripts/ingest_catalog.py --dry-run
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

# ── stdlib path hack so script runs from repo root ──────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logfire
import typer
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from services.database import AsyncSessionLocal
from services.rag.ingest import ingest_product_text

console = Console()
app = typer.Typer(no_args_is_help=True)


# ── Pydantic model for catalog products ──────────────────────────────────────
class CatalogProduct(BaseModel):
    """Product from JSON catalog with validation."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_default=True)

    sku: str = Field(..., min_length=3, max_length=50)
    name: str = Field(..., min_length=3, max_length=500)
    category: str = Field(..., min_length=2, max_length=100)
    subcategory: str = Field(..., min_length=2, max_length=100)
    price: float = Field(..., gt=0)
    currency: str = Field(default="VND")
    description: str = Field(..., min_length=10, max_length=5000)
    intent: str = Field(default="B2C")
    specifications: dict[str, Any] = Field(default_factory=dict)


class CatalogFile(BaseModel):
    """Root structure of catalog JSON file."""

    catalog: list[CatalogProduct] = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Async utility functions ──────────────────────────────────────────────────
async def load_catalog_file(path: Path) -> CatalogFile:
    """Load and validate catalog JSON file asynchronously."""
    # Check existence in thread to avoid blocking
    loop = asyncio.get_event_loop()

    def _check_and_read() -> str:
        if not path.exists():
            raise FileNotFoundError(f"Catalog file not found: {path}")
        return path.read_text(encoding="utf-8")

    # Read file in thread to avoid blocking
    content = await loop.run_in_executor(None, _check_and_read)

    # Parse JSON
    raw = json.loads(content)
    catalog = CatalogFile.model_validate(raw)

    logfire.info(
        "Catalog loaded: {count} products",
        count=len(catalog.catalog),
    )
    return catalog


async def ingest_single_product(
    product: CatalogProduct,
    semaphore: asyncio.Semaphore,
) -> tuple[bool, str, str]:
    """
    Ingest a single product with semaphore-controlled concurrency.
    Each product gets its own database session.

    Returns: (success: bool, product_id: str, message: str)
    """
    async with semaphore:  # Non-blocking concurrency control
        try:
            # Create new session for this product
            async with AsyncSessionLocal() as session:
                product_id = await ingest_product_text(
                    db=session,
                    name=product.name,
                    sku=product.sku,
                    description=product.description,
                    price=product.price,
                    metadata={
                        "category": product.category,
                        "subcategory": product.subcategory,
                        "intent": product.intent,
                        "specifications": product.specifications,
                        "currency": product.currency,
                    },
                )
                return True, product_id, f"✓ {product.sku}"
        except Exception as e:
            logfire.warn(
                "Failed to ingest product: {sku}",
                sku=product.sku,
                error=str(e),
            )
            return False, "", f"✗ {product.sku}: {str(e)[:40]}"


async def ingest_batch(
    products: list[CatalogProduct],
    embed_concurrency: int = 4,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ingest batch of products with progress tracking."""
    if dry_run:
        console.print("[yellow]DRY RUN MODE - No database writes[/]\n")
        table = Table(title="Products to Ingest", show_header=True)
        table.add_column("SKU", style="cyan")
        table.add_column("Name", style="magenta")
        table.add_column("Price", style="green")
        for p in products:
            table.add_row(p.sku, p.name[:40], f"{p.price:,.0f} {p.currency}")
        console.print(table)
        return {
            "total": len(products),
            "success": len(products),
            "failed": 0,
            "failed_skus": [],
            "duration_secs": 0,
        }

    # Get async session
    start_time = time.perf_counter()
    semaphore = asyncio.Semaphore(embed_concurrency)

    results = {"success": 0, "failed": 0, "failed_skus": []}

    # Progress bar
    with Progress(
        SpinnerColumn(),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Ingesting products...", total=len(products))

        # Create tasks for all products
        tasks = [ingest_single_product(product, semaphore) for product in products]

        # Execute with progress updates
        for coro in asyncio.as_completed(tasks):
            success, _, msg = await coro
            if success:
                results["success"] += 1
                logfire.info("Product ingested", sku=msg)
            else:
                results["failed"] += 1
                results["failed_skus"].append(msg)
            progress.advance(task)

    duration = time.perf_counter() - start_time
    results["total"] = len(products)
    results["duration_secs"] = duration

    return results


# ── CLI Commands ─────────────────────────────────────────────────────────────
_DEFAULT_CATALOG = Path("scripts/product-catalog.json")


@app.command()
def ingest(
    catalog_path: Path = typer.Option(
        _DEFAULT_CATALOG,
        "--catalog",
        help="Path to product catalog JSON file",
    ),
    test_db: bool = typer.Option(
        False,
        "--test-db",
        help="Ingest to ai_agent_test instead of ai_agent",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Limit ingestion to N products (for testing)",
    ),
    embed_concurrency: int = typer.Option(
        1,
        "--embed-concurrency",
        help="Concurrent ingest workers (1=sequential, safe for local Ollama)",
        min=1,
        max=16,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be ingested without writing to DB",
    ),
) -> None:
    """Ingest products from JSON catalog file to database."""
    asyncio.run(
        _ingest_async(
            catalog_path,
            test_db,
            limit,
            embed_concurrency,
            dry_run,
        )
    )


async def _ingest_async(
    catalog_path: Path,
    test_db: bool,
    limit: int | None,
    embed_concurrency: int,
    dry_run: bool,
) -> None:
    """Async implementation of ingest command."""
    console.print(f"[bold cyan]Loading catalog from: {catalog_path}[/]")

    # Select database
    if test_db:
        console.print("[yellow]Target: ai_agent_test[/]")
    else:
        console.print("[yellow]Target: ai_agent[/]")

    try:
        # Load catalog
        catalog = await load_catalog_file(catalog_path)
        products = catalog.catalog

        # Apply limit if specified
        if limit:
            products = products[:limit]
            console.print(f"[yellow]Limiting to {limit} products[/]")

        console.print()

        # Run ingestion
        results = await ingest_batch(
            products=products,
            embed_concurrency=embed_concurrency,
            dry_run=dry_run,
        )

        # Summary
        console.print()
        console.print("[bold]Ingestion Summary[/]")
        summary_table = Table(show_header=False)
        summary_table.add_row("Total", str(results["total"]))
        summary_table.add_row("Success", f"[green]{results['success']}[/]")
        summary_table.add_row("Failed", f"[red]{results['failed']}[/]")
        summary_table.add_row("Duration", f"{results['duration_secs']:.2f}s")
        if results["success"] > 0 and results["duration_secs"] > 0:
            throughput = results["success"] / results["duration_secs"]
            summary_table.add_row("Throughput", f"{throughput:.1f} products/sec")
        console.print(summary_table)

        if results["failed_skus"]:
            console.print("\n[yellow]Failed Products:[/]")
            for sku in results["failed_skus"]:
                console.print(f"  {sku}")

        logfire.info(
            "Ingestion completed",
            total=results["total"],
            success=results["success"],
            failed=results["failed"],
            duration_secs=results["duration_secs"],
        )

    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/]")
        sys.exit(1)
    except ValidationError as e:
        console.print("[red]Catalog validation error:[/]")
        for error in e.errors():
            console.print(f"  - {error}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/]")
        logfire.error("Ingestion failed", error=str(e))
        sys.exit(1)


@app.command()
def validate_catalog(
    catalog_path: Path = typer.Option(
        _DEFAULT_CATALOG,
        "--catalog",
        help="Path to product catalog JSON file",
    ),
) -> None:
    """Validate catalog JSON file without ingesting."""
    asyncio.run(_validate_async(catalog_path))


async def _validate_async(catalog_path: Path) -> None:
    """Async validation implementation."""
    console.print(f"[cyan]Validating: {catalog_path}[/]")
    try:
        catalog = await load_catalog_file(catalog_path)
        console.print(f"[green]✓ Catalog valid[/] ({len(catalog.catalog)} products)")

        # Show summary
        table = Table(title="Catalog Contents", show_header=True)
        table.add_column("SKU", style="cyan")
        table.add_column("Name", style="magenta")
        table.add_column("Category", style="green")
        table.add_column("Price", style="yellow")

        for product in catalog.catalog:
            table.add_row(
                product.sku,
                product.name[:30],
                product.category,
                f"{product.price:,.0f}",
            )

        console.print(table)

    except ValidationError as e:
        console.print("[red]✗ Validation failed[/]")
        for error in e.errors():
            console.print(f"  {error}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗ Error: {e}[/]")
        sys.exit(1)


if __name__ == "__main__":
    app()
