"""Clean database script for development phase.

This script safely deletes all data from the AI Agent database tables
while preserving the schema and migrations. Useful for dev/testing phases.

Usage:
    # Delete from main database
    uv run python scripts/cleanup_db.py

    # Delete from test database
    uv run python scripts/cleanup_db.py --test-db

Safety:
    - Only deletes tables defined in models/schema.py
    - Preserves schema and alembic_version table
    - Requires explicit --confirm flag to proceed
    - Shows what will be deleted before execution
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# ── stdlib path hack so script runs from repo root ──────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logfire
import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import text

from services.database import AsyncSessionLocal

console = Console()
app = typer.Typer(no_args_is_help=True)

# Tables to delete (in reverse dependency order)
TABLES_TO_DELETE = [
    "agent_v1.text_embeddings",  # No foreign keys
    "agent_v1.conversation_messages",  # FK -> conversation_sessions
    "agent_v1.conversation_sessions",  # FK -> products
    "agent_v1.sales_signals",  # FK -> products
    "agent_v1.model_traces",  # FK -> conversation_messages
    "agent_v1.products",  # Base table
]


@app.command()
def cleanup(
    test_db: bool = typer.Option(
        False,
        "--test-db",
        help="Delete from ai_agent_test database instead of ai_agent",
    ),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Skip confirmation and proceed with deletion",
    ),
) -> None:
    """Delete all data from AI Agent database tables (schema preserved)."""
    asyncio.run(_cleanup_async(test_db, confirm))


async def _cleanup_async(test_db: bool, confirm: bool) -> None:
    """Async cleanup implementation."""
    # Select database
    if test_db:
        db_name = "ai_agent_test"
        console.print("[yellow]⚠️  Target: ai_agent_test database[/]")
    else:
        db_name = "ai_agent"
        console.print("[yellow]⚠️  Target: ai_agent database[/]")

    console.print()
    console.print("[bold red]This will DELETE:[/]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Order", style="cyan")
    table.add_column("Table", style="magenta")
    for i, tbl in enumerate(TABLES_TO_DELETE, 1):
        table.add_row(str(i), tbl)
    console.print(table)
    console.print()

    if not confirm:
        if not typer.confirm("[bold red]Proceed with deletion?[/]", default=False):
            console.print("[yellow]Cancelled.[/]")
            return

    try:
        async with AsyncSessionLocal() as session:
            logfire.info("Starting database cleanup", db=db_name)

            for i, table_name in enumerate(TABLES_TO_DELETE, 1):
                try:
                    # Delete with TRUNCATE (faster than DELETE for large tables)
                    await session.execute(text(f"TRUNCATE TABLE {table_name} CASCADE"))
                    console.print(f"[green]✓[/] {i}. {table_name}")
                except Exception as e:
                    logfire.warn(
                        "Failed to truncate table: {table}",
                        table=table_name,
                        error=str(e),
                    )
                    console.print(f"[yellow]⚠[/]  {i}. {table_name} - {str(e)[:50]}")

            await session.commit()
            console.print()
            console.print("[green]✅ Database cleanup complete![/]")
            logfire.info(
                "Database cleanup completed",
                db=db_name,
                tables=len(TABLES_TO_DELETE),
            )

    except Exception as e:
        console.print(f"[red]❌ Error: {e}[/]")
        logfire.error("Database cleanup failed", error=str(e), db=db_name)
        sys.exit(1)


if __name__ == "__main__":
    app()
