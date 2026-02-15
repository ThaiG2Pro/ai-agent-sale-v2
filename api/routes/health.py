"""Why this exists: Verifies system health and responsiveness.
What it does: Implements a health check endpoint that verifies DB connectivity.
"""

import time
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.database import get_db

router = APIRouter()


@router.get("/health")
async def health_check(db: Annotated[AsyncSession, Depends(get_db)]):
    """
    Why this exists: Verifies core system components are operational.
    SC-002 Target: < 10ms response time.
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
