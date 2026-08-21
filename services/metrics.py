"""
Why this exists: v3-0 P2 (T07/2.3) — the 80/20 contract has an acceptance
metric (deflection rate per session ≥ 80%) but nothing computed it; the gap
was flagged as "không đo được 80/20" in the locked proposal.
What it does: Computes the deflection rate from existing Postgres tables
(support_queue + interrupted_sessions as the handoff set, sales_intent_logs
as the tracked-session universe) plus small queue-health counters. No new
tables, no background jobs — read-only aggregate queries for GET /admin/metrics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from models.schema import HITLMetadata, InterruptedSession, SalesIntentLog, SupportQueue

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _distinct_sessions(db: AsyncSession, column) -> set[str]:
    res = await db.execute(select(column).distinct())
    return {row[0] for row in res.all() if row[0]}


async def get_agent_metrics(db: AsyncSession) -> dict[str, Any]:
    """Deflection rate per session + queue-health counters (v3-0 P2 2.3).

    A session counts as "handed off" when it appears in support_queue OR
    interrupted_sessions. The session universe is the union of tracked
    sessions (sales_intent_logs.thread_id — written every turn by the memory
    pipeline) and the handoff set itself, so a handoff session missing from
    the intent log can never push the rate above 1.0.
    """
    handoff_sessions = await _distinct_sessions(
        db, SupportQueue.session_id
    ) | await _distinct_sessions(db, InterruptedSession.session_id)
    tracked_sessions = await _distinct_sessions(db, SalesIntentLog.thread_id)

    total_sessions = tracked_sessions | handoff_sessions
    total = len(total_sessions)
    handed_off = len(handoff_sessions)
    deflection_rate = (total - handed_off) / total if total else None

    queue_depth = (
        await db.execute(
            select(func.count()).select_from(SupportQueue).where(SupportQueue.status == "pending")
        )
    ).scalar_one()
    paused_count = (
        await db.execute(
            select(func.count()).select_from(HITLMetadata).where(HITLMetadata.status == "paused")
        )
    ).scalar_one()

    # v3-0 P3 (T12 3.4): degraded turn counter (in-process, since start).
    from services import resilience

    return {
        "deflection_rate": deflection_rate,
        "deflection_target": 0.80,
        "total_sessions": total,
        "handoff_sessions": handed_off,
        "support_queue_depth": queue_depth,
        "hitl_paused_count": paused_count,
        "degraded_turns": resilience.degraded_turn_count(),
    }
