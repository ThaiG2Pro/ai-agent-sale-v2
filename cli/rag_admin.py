"""RAG Administration CLI with Typer and Rich formatting.

Why this exists: Modern CLI for managing the RAG pipeline.
What it does: Provides Typer-based CLI with Rich for Ingest, Search, and Stats.
Article IV: Zero-Cost Baseline - Local-first with API fallback.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from functools import wraps

import httpx
import typer
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import func, select

from core.config import settings
from core.logging import setup_logging
from models.schema import Product, TextEmbedding
from services.database import AsyncSessionLocal
from services.rag import answer_with_rag, ingest_product_text, search_products

app = typer.Typer(
    name="rag-admin",
    help="RAG Administration CLI - Manage products, search, and monitor embeddings.",
    pretty_exceptions_show_locals=True,
)
console = Console()


def async_command(func):
    """Decorator to handle async functions in Typer commands.
    Solves the Typer+async issue by wrapping async functions.
    Uses functools.wraps to preserve metadata for Typer command registration.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wrapper


async def call_api(
    endpoint: str,
    data: dict,
    api_url: str,
    request_timeout: float = 30.0,
):
    """Why this exists: Centralized API caller for RAG admin.
    What it does: Sends POST request with X-Admin-Key to the API.
    request_timeout: caller sets per-endpoint — ingest needs 300s
    (Ollama serializes model loads).
    """
    headers = {"X-Admin-Key": settings.X_ADMIN_KEY}
    async with httpx.AsyncClient(timeout=request_timeout) as client:
        response = await client.post(
            f"{api_url.rstrip('/')}/admin/rag/{endpoint}", json=data, headers=headers
        )
        response.raise_for_status()
        return response.json()


async def _ingest_local(
    name: str, sku: str, description: str, price: float, metadata: dict | None
):
    """Local database ingestion."""
    async with AsyncSessionLocal() as db:
        try:
            product_id = await ingest_product_text(
                db=db,
                name=name,
                sku=sku,
                description=description,
                price=price,
                metadata=metadata or {},
            )

            # Fetch & display metadata for debug
            stmt = select(Product).where(Product.id == product_id)
            result = await db.execute(stmt)
            product = result.scalar_one()

            metadata_info = Panel(
                f"[cyan]Product ID:[/cyan] {product_id}\n"
                f"[cyan]SKU:[/cyan] {product.sku}\n"
                f"[cyan]Name:[/cyan] {product.name}\n"
                f"[cyan]Price:[/cyan] {product.price}\n"
                f"[cyan]Created At:[/cyan] {product.created_at}\n"
                f"[cyan]Metadata:[/cyan] {json.dumps(product.metadata_, indent=2)}\n",
                title="✓ Ingestion Success (Local)",
                border_style="green",
            )
            console.print(metadata_info)
            return True
        except Exception as e:
            console.print(
                f"✗ Local Ingestion failed: {e}",
                style="bold red",
            )
            return False


async def _ingest_api(
    name: str,
    sku: str,
    description: str,
    price: float,
    metadata: dict | None,
    api_url: str,
):
    """API-based ingestion."""
    try:
        payload = {
            "name": name,
            "sku": sku,
            "description": description,
            "price": price,
            "metadata": metadata or {},
        }
        console.print(f"[cyan]📡 Calling:[/cyan] {api_url}/admin/rag/ingest...")
        console.print(
            "[dim]  (ingest includes LLM enrichment — "
            "may take 2-3 min with Ollama)[/dim]"
        )
        result = await call_api("ingest", payload, api_url, request_timeout=300.0)

        metadata_info = Panel(
            f"[cyan]Product ID:[/cyan] {result.get('product_id')}\n"
            f"[cyan]API Response:[/cyan] {json.dumps(result, indent=2)}\n",
            title="✓ Ingestion Success (API)",
            border_style="green",
        )
        console.print(metadata_info)
        return True
    except Exception as e:
        import traceback

        console.print(f"[red]✗ API Ingestion failed: {type(e).__name__}: {e}[/red]")
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        console.print("[yellow]💡 Hint: If server is not running, use --local[/yellow]")
        return False


@app.command()
@async_command
async def ingest(
    name: str = typer.Option(..., help="Product name"),
    sku: str = typer.Option(..., help="Product SKU (unique identifier)"),
    description: str = typer.Option(..., help="Product description for embedding"),
    price: float = typer.Option(0.0, help="Product price"),
    metadata: str | None = typer.Option(
        None, help="JSON string of metadata for debugging RAG"
    ),
    local: bool = typer.Option(
        False, "--local", help="Run directly against database (offline mode)"
    ),
    api_url: str = typer.Option(
        "http://localhost:8000", help="Web API URL for non-local mode"
    ),
):
    """Ingest a product into the RAG system.

    Examples:
        rag-admin ingest --name "Laptop Pro" --sku "LP-001" \\
          --description "High-performance laptop" --price 1500.00
        rag-admin ingest --name "Mouse" --sku "M-001" \\
          --description "Wireless mouse with 2.4GHz" \\
          --metadata '{"color": "black", "warranty_months": 24}'
        rag-admin ingest --local --name "Test" --sku "TEST-123" \\
          --description "For testing"
    """
    try:
        meta_dict = None
        if metadata:
            try:
                meta_dict = json.loads(metadata)
            except json.JSONDecodeError:
                console.print("[red]✗ Invalid JSON in --metadata[/red]", style="bold")
                sys.exit(1)

        if local:
            success = await _ingest_local(name, sku, description, price, meta_dict)
        else:
            success = await _ingest_api(
                name, sku, description, price, meta_dict, api_url
            )

        sys.exit(0 if success else 1)
    except Exception as e:
        console.print(f"[red]✗ Unexpected error: {e}[/red]", style="bold")
        sys.exit(1)


@app.command()
@async_command
async def search(
    query: str = typer.Argument(..., help="Search query string"),
    top_k: int = typer.Option(
        5, "--top-k", help="Number of results to return (default: 5)"
    ),
    local: bool = typer.Option(
        False, "--local", help="Run directly against database (offline mode)"
    ),
    api_url: str = typer.Option(
        "http://localhost:8000", help="Web API URL for non-local mode"
    ),
):
    """Search for products using semantic similarity.

    Examples:
        rag-admin search "laptop price"
        rag-admin search "wireless mouse" --top-k 10
        rag-admin search --local "gaming laptop"
    """
    try:
        if not local:
            try:
                payload = {"query": query, "top_k": top_k}
                console.print(f"[cyan]📡 Calling:[/cyan] {api_url}/admin/rag/search...")
                results = await call_api("search", payload, api_url)
                _display_search_results(results, metadata={"mode": "api"})
                return
            except Exception as e:
                msg = (
                    f"✗ API Search failed: {e}\n"
                    "💡 Hint: If server is not running, use --local"
                )
                console.print(msg, style="bold red")
                sys.exit(1)

        # Local Fallback
        async with AsyncSessionLocal() as db:
            try:
                results = await search_products(db=db, query=query, top_k=top_k)
                _display_search_results(results, metadata={"mode": "local"})
            except Exception as e:
                console.print(f"✗ Local Search failed: {e}", style="bold red")
                sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗ Unexpected error: {e}[/red]", style="bold")
        sys.exit(1)


def _display_search_results(results: list, metadata: dict | None = None):
    """Display search results in a formatted table with metadata."""
    if not results:
        console.print("[yellow]⚠ No results found[/yellow]")
        return

    table = Table(title="Search Results", show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim")
    table.add_column("SKU")
    table.add_column("Name", style="cyan")
    table.add_column("Similarity", style="green")
    table.add_column("Price", style="yellow")

    for hit in results:
        table.add_row(
            str(hit["id"])[:8] + "...",
            hit["sku"],
            hit["name"],
            f"{hit['score']:.4f}",
            f"${hit.get('price', 'N/A')}",
        )

    console.print(table)

    # Display metadata
    if metadata:
        meta_panel = Panel(
            f"[cyan]Mode:[/cyan] {metadata.get('mode', 'unknown')}\n"
            f"[cyan]Total Results:[/cyan] {len(results)}\n"
            f"[cyan]Query Executed At:[/cyan] {datetime.now().isoformat()}\n",
            title="Search Metadata",
            border_style="blue",
        )
        console.print(meta_panel)

    # Show detailed metadata option
    if results:
        console.print(
            "\n[dim]💡 For full details including metadata, use:[/dim] "
            "[cyan]rag-admin search <query> | grep -i metadata[/cyan]"
        )
        console.print(
            "[dim]   Or export as JSON for programmatic use:[/dim] "
            "[cyan]rag-admin search <query> --json[/cyan]"
        )


@app.command()
@async_command
async def query(
    question: str = typer.Argument(..., help="Question to ask the RAG pipeline"),
    model: str = typer.Option(
        "economy-chat", "--model", help="LLM model to use (default: economy-chat)"
    ),
    local: bool = typer.Option(
        False, "--local", help="Run directly against database (offline mode)"
    ),
    api_url: str = typer.Option(
        "http://localhost:8000", help="Web API URL for non-local mode"
    ),
):
    """Ask the RAG pipeline a question using semantic search + LLM.

    Examples:
        rag-admin query "What is the price of iPhone 13?"
        rag-admin query --local "Compare laptop prices"
        rag-admin query "Tell me about your products" --model economy-chat
    """
    if not local:
        try:
            payload = {"query": question, "model": model}
            console.print(
                f"[cyan]📡 Calling:[/cyan] {api_url}/query (model: {model})..."
            )
            console.print(
                "[dim]  (first query may take 60-120s while Ollama loads model)[/dim]"
            )
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(
                    f"{api_url.rstrip('/')}/query", json=payload
                )
                response.raise_for_status()
                result = response.json()

            _display_rag_result(result, metadata={"mode": "api"})
            return
        except Exception as e:
            import traceback

            console.print(f"[red]✗ API Query failed: {type(e).__name__}: {e}[/red]")
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
            console.print(
                "[yellow]💡 Hint: If server is not running, use --local[/yellow]"
            )
            sys.exit(1)

    # Local Query
    async with AsyncSessionLocal() as db:
        try:
            result = await answer_with_rag(db=db, query=question, model=model)
            _display_rag_result(result, metadata={"mode": "local"})
        except Exception as e:
            console.print(f"✗ Local Query failed: {e}", style="bold red")
            import traceback

            traceback.print_exc()
            sys.exit(1)


def _display_rag_result(result, metadata: dict | None = None):
    """Display RAG result with answer, citations, and metrics."""
    # Convert Pydantic model to dict if needed
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    elif hasattr(result, "dict"):
        result = result.dict()

    # Display main answer
    answer_panel = Panel(
        result.get("answer", "No answer generated"),
        title="🤖 RAG Answer",
        border_style="green" if not result.get("declined") else "yellow",
    )
    console.print(answer_panel)

    # Display citations if available
    citations = result.get("citations", [])
    if citations:
        citations_table = Table(
            title="📚 Citations", show_header=True, header_style="bold cyan"
        )
        citations_table.add_column("SKU", style="dim")
        citations_table.add_column("Product Name", style="cyan")
        citations_table.add_column("Product ID", style="dim")

        for citation in citations:
            citations_table.add_row(
                citation.get("sku", "N/A"),
                citation.get("name", "N/A"),
                str(citation.get("product_id", "N/A"))[:8] + "...",
            )

        console.print(citations_table)
    else:
        if not result.get("declined"):
            console.print("[yellow]⚠ No citations found[/yellow]")

    # Display metrics
    status_str = "❌ Declined" if result.get("declined") else "✅ Answered"
    chunks_before = result.get("chunks_before_compression", 0)
    chunks_after = result.get("chunks_after_compression", 0)
    metrics_panel = Panel(
        (
            f"[cyan]Status:[/cyan] {status_str}\n"
            f"[cyan]Best Similarity:[/cyan] "
            f"{result.get('best_similarity', 0):.4f}\n"
            f"[cyan]Query Category:[/cyan] "
            f"{result.get('query_category', 'unknown')}\n"
            f"[cyan]TopK Used:[/cyan] {result.get('top_k_used', 'N/A')}\n"
            f"[cyan]Model Used:[/cyan] {result.get('model_used', 'N/A')}\n"
            f"[cyan]Chunks (before/after compression):[/cyan] "
            f"{chunks_before}/{chunks_after}\n"
            f"[cyan]Query Executed At:[/cyan] "
            f"{datetime.now().isoformat()}\n"
        ),
        title="📊 RAG Metrics",
        border_style="blue",
    )
    console.print(metrics_panel)

    # Display metadata
    if metadata:
        meta_panel = Panel(
            f"[cyan]Mode:[/cyan] {metadata.get('mode', 'unknown')}\n",
            title="Execution Info",
            border_style="dim",
        )
        console.print(meta_panel)


@app.command()
@async_command
async def stats(
    local: bool = typer.Option(
        False, "--local", help="Run directly against database (offline mode)"
    ),
    api_url: str = typer.Option(
        "http://localhost:8000", help="Web API URL for non-local mode"
    ),
):
    """Display RAG system statistics and embeddings metadata.

    Shows:
      - Total products in the system
      - Total embeddings computed
      - Embedding model and version in use
      - Storage metrics
    """
    try:
        if not local:
            try:
                console.print(f"[cyan]📡 Calling:[/cyan] {api_url}/admin/rag/stats...")
                stats_data = await call_api("stats", {}, api_url)
                _display_stats(stats_data, metadata={"mode": "api"})
                return
            except Exception as e:
                msg = (
                    f"✗ API Stats failed: {e}\n"
                    "💡 Hint: If server is not running, use --local"
                )
                console.print(msg, style="bold red")
                sys.exit(1)

        # Local stats
        async with AsyncSessionLocal() as db:
            try:
                # Count products
                product_count_stmt = select(func.count(Product.id))
                product_count = await db.scalar(product_count_stmt)

                # Count embeddings
                embedding_count_stmt = select(func.count(TextEmbedding.id))
                embedding_count = await db.scalar(embedding_count_stmt)

                # Get embedding models in use
                embedding_models_stmt = select(
                    TextEmbedding.model_name,
                    TextEmbedding.model_version,
                    func.count(TextEmbedding.id).label("count"),
                ).group_by(TextEmbedding.model_name, TextEmbedding.model_version)
                embedding_models = await db.execute(embedding_models_stmt)
                models = embedding_models.all()

                stats_data = {
                    "total_products": product_count or 0,
                    "total_embeddings": embedding_count or 0,
                    "embedding_models": [
                        {
                            "name": model[0],
                            "version": model[1],
                            "count": model[2],
                        }
                        for model in models
                    ],
                }
                _display_stats(stats_data, metadata={"mode": "local"})
            except Exception as e:
                console.print(f"✗ Local Stats failed: {e}", style="bold red")
                sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗ Unexpected error: {e}[/red]", style="bold")
        sys.exit(1)


def _display_stats(stats_data: dict, metadata: dict | None = None):
    """Display statistics in a formatted panel."""
    mode_str = metadata.get("mode", "unknown") if metadata else "unknown"
    stats_panel = Panel(
        f"[cyan]Total Products:[/cyan] {stats_data['total_products']}\n"
        f"[cyan]Total Embeddings:[/cyan] {stats_data['total_embeddings']}\n"
        f"[cyan]Mode:[/cyan] {mode_str}\n"
        f"[cyan]Captured At:[/cyan] {datetime.now().isoformat()}",
        title="RAG System Statistics",
        border_style="green",
    )
    console.print(stats_panel)

    # Print embedding models table separately
    if stats_data.get("embedding_models"):
        models_table = Table(
            title="Embedding Models", show_header=True, header_style="bold cyan"
        )
        models_table.add_column("Model Name")
        models_table.add_column("Version")
        models_table.add_column("Count", style="green")

        for model in stats_data["embedding_models"]:
            models_table.add_row(
                model["name"], model["version"] or "N/A", str(model["count"])
            )
        console.print(models_table)


def main():
    """Entry point for the CLI application."""
    setup_logging()
    HTTPXClientInstrumentor().instrument()
    app()


if __name__ == "__main__":
    main()
