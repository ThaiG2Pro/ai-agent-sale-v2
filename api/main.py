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
from api.routes import admin, agent, health, query
from core.logging import instrument_fastapi, instrument_sqlalchemy, setup_logging
from services.database import engine

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

    logfire.info("Application foundation initialized successfully.")

    # Warm up economy-chat model in background (avoid cold start on first query)
    import asyncio

    _warmup_task = asyncio.create_task(_warmup_model())  # noqa: RUF006

    yield

    # Shutdown logic
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


@app.get("/")
async def root():
    return {"message": "AI Sales Agent Infrastructure is running."}
