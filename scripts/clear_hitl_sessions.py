"""Script to clear old HITL sessions and active conversation threads in dev database."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console
from sqlalchemy import text

from services.database import AsyncSessionLocal

console = Console()

TABLES_TO_CLEAR = [
    "agent_v1.hitl_metadata",
    "agent_v1.interrupted_sessions",
    "agent_v1.support_queue",
    "agent_v1.queued_messages",
    "agent_v1.review_actions",
    "agent_v1.sales_signals",
    "agent_v1.sales_intent_logs",
    "agent_v1.intent_tracking",
    "agent_v1.episodic_events",
    "agent_v1.conversation_messages",
    "agent_v1.conversation_sessions",
]


async def main() -> None:
    console.print("[bold yellow]🧹 Cleaning old HITL & Conversation sessions...[/]")
    async with AsyncSessionLocal() as session:
        for tbl in TABLES_TO_CLEAR:
            try:
                await session.execute(text(f"TRUNCATE TABLE {tbl} CASCADE"))
                console.print(f"[green]✓ Cleared:[/] {tbl}")
            except Exception as e:
                console.print(f"[yellow]⚠ Failed to clear {tbl}: {e}[/]")
        await session.commit()
    console.print("[bold green]✨ HITL sessions cleared successfully![/]")


if __name__ == "__main__":
    asyncio.run(main())
