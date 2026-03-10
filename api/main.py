"""Why this exists: Entry point for the AI Sales Agent API.
What it does: Initializes FastAPI app, registers middleware, and configures
lifecycle events.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import logfire
from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from api.middleware import (
    TimingMiddleware,
    global_exception_handler,
    http_exception_handler,
)
from api.routes import admin, agent, health, hitl, query
from core.agent.checkpointer import create_checkpointer
from core.config import settings
from core.logging import instrument_fastapi, instrument_sqlalchemy, setup_logging
from services.database import engine, session_factory
from services.hitl.timeout_scheduler import run_timeout_scheduler

# Initialize observability FIRST — sets OTel TracerProvider → Phoenix.
# Must run before any instrumented code or FastAPI app creation.
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Why this exists: Manages system startup and shutdown.
    What it does: Initializes logging and verifies DB connectivity.
    Warms up economy-chat model to avoid cold start on first query.
    """
    # Verify DB connectivity (Article VII)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))

    # Wire SQLAlchemy engine to OTel — must happen after engine is created
    instrument_sqlalchemy(engine.sync_engine)

    # Initialize HITL Checkpointer (T053)
    checkpointer = await create_checkpointer(settings.database_url_psycopg)
    app.state.checkpointer = checkpointer

    logfire.info("Application foundation initialized successfully.")

    # Initialize background task storage
    app.state.background_tasks = set()

    # Verify Telegram Configuration (T073)
    if not settings.TELEGRAM_BOT_TOKEN or settings.TELEGRAM_BOT_TOKEN == "your_bot_token_here":
        logfire.warn("TELEGRAM_BOT_TOKEN not configured. Administrative alerts will be disabled.")

    # Start HITL Timeout Scheduler (Phase 15)
    import asyncio

    timeout_task = asyncio.create_task(run_timeout_scheduler(session_factory=session_factory))
    app.state.background_tasks.add(timeout_task)
    timeout_task.add_done_callback(app.state.background_tasks.discard)

    # Start Nightly Archive Task (T070)
    from services.hitl.archive_scheduler import run_nightly_archive

    archive_task = asyncio.create_task(run_nightly_archive(session_factory=session_factory))
    app.state.background_tasks.add(archive_task)
    archive_task.add_done_callback(app.state.background_tasks.discard)

    # Warm up economy-chat model in background (avoid cold start on first query)
    warmup_task = asyncio.create_task(_warmup_model())
    app.state.background_tasks.add(warmup_task)
    warmup_task.add_done_callback(app.state.background_tasks.discard)

    yield

    # Shutdown logic
    # Close checkpointer connection/pool (psycopg3)
    if hasattr(app.state, "checkpointer"):
        conn_or_pool = getattr(app.state.checkpointer, "conn", None)
        if conn_or_pool is not None and hasattr(conn_or_pool, "close"):
            await conn_or_pool.close()

    await engine.dispose()
    logfire.info("Application shutdown complete.")


async def _warmup_model() -> None:
    """Pre-load economy-chat model so first query doesn't cold-start."""
    try:
        from services.ai import AIGateway

        await AIGateway.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="economy-chat",
        )
        logfire.info("Model warmup complete: economy-chat ready")
    except Exception as exc:
        logfire.warn("Model warmup failed (non-critical): {err}", err=str(exc))


app = FastAPI(
    title="AI Sales Agent Foundation",
    description="SME-Ready AI Sales Agent (2026) Infrastructure",
    version="0.1.0",
    lifespan=lifespan,
)

# Wire FastAPI app to OTel — creates HTTP request spans for every endpoint
instrument_fastapi(app)

# Register Middleware (T010)
app.add_middleware(TimingMiddleware)

# Register Exception Handlers (T010)
app.add_exception_handler(Exception, global_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)

# Register Routes (T023 Week 2, new Week 3 agent routes)
app.include_router(health.router)
app.include_router(admin.router)
app.include_router(query.router)
app.include_router(agent.router)
app.include_router(hitl.router)


@app.get("/")
async def root():
    return {"message": "AI Sales Agent Infrastructure is running."}
