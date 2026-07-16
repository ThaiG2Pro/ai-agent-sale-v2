"""Why this exists: Verifies system health and responsiveness.
What it does: Implements a health check endpoint that verifies DB connectivity.
"""

from __future__ import annotations

import asyncio
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncSession,  # noqa: TC002 - NEEDED: for Pydantic schema resolution
)

from core.config import settings
from services.database import engine, get_db

router = APIRouter()


@router.get("/health")
async def health_check(db: Annotated[AsyncSession, Depends(get_db)]):
    """
    Why this exists: Verifies core system components are operational.
    SC-002 Target: < 10ms response time.

    NOTE — liveness-style by design: this endpoint always returns HTTP 200,
    even when the DB is down (body reports status="degraded"). Load balancers
    and orchestrators MUST use /health/readiness, which returns 503 when
    dependencies are not ready. Do NOT wire /health into traffic-gating checks.
    """
    start_time = time.perf_counter()

    # Verify DB connectivity (Article VII)
    try:
        await db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "error"

    latency_ms = (time.perf_counter() - start_time) * 1000

    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "db": db_status,
        "latency_ms": latency_ms,
    }


@router.get("/health/liveness")
async def health_liveness() -> dict[str, float | str]:
    return {
        "status": "alive",
        "timestamp": time.time(),
    }


@router.get("/health/readiness")
async def health_readiness(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    checks: dict[str, dict[str, object]] = {}
    overall_status = "ready"

    db_start = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = {
            "status": "ok",
            "latency_ms": (time.perf_counter() - db_start) * 1000,
        }
    except SQLAlchemyError as exc:
        overall_status = "not_ready"
        checks["database"] = {
            "status": "error",
            "latency_ms": (time.perf_counter() - db_start) * 1000,
            "reason": str(exc),
        }

    event_loop_start = time.perf_counter()
    await asyncio.sleep(0)
    checks["event_loop"] = {
        "status": "ok",
        "latency_ms": (time.perf_counter() - event_loop_start) * 1000,
    }

    pool = engine.sync_engine.pool
    checked_out = pool.checkedout() if hasattr(pool, "checkedout") else 0
    max_size = settings.DB_POOL_SIZE
    if checked_out >= max_size:
        if overall_status == "ready":
            overall_status = "degraded"
        checks["connection_pool"] = {
            "status": "degraded",
            "size": checked_out,
            "max_size": max_size,
            "reason": f"Connection pool is exhausted ({checked_out}/{max_size})",
        }
    else:
        checks["connection_pool"] = {
            "status": "ok",
            "size": checked_out,
            "max_size": max_size,
        }

    if overall_status == "not_ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": overall_status,
        "checks": checks,
        "timestamp": time.time(),
    }
