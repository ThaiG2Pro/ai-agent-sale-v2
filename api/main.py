"""Why this exists: Entry point for the AI Sales Agent API.
What it does: Initializes FastAPI app, registers middleware, and configures
lifecycle events.
"""

from contextlib import asynccontextmanager

import logfire
from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from api.middleware import (
    TimingMiddleware,
    global_exception_handler,
    http_exception_handler,
)
from api.routes import health
from core.logging import setup_logging
from services.database import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Why this exists: Manages system startup and shutdown.
    What it does: Initializes logging and verifies DB connectivity.
    """
    # Initialize Logging (T009)
    setup_logging()

    # Verify DB connectivity (Article VII)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))

    logfire.info("Application foundation initialized successfully.")

    yield

    # Shutdown logic
    await engine.dispose()
    logfire.info("Application shutdown complete.")


app = FastAPI(
    title="AI Sales Agent Foundation",
    description="SME-Ready AI Sales Agent (2026) Infrastructure",
    version="0.1.0",
    lifespan=lifespan,
)

# Register Middleware (T010)
app.add_middleware(TimingMiddleware)

# Register Exception Handlers (T010)
app.add_exception_handler(Exception, global_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)

# Register Routes (T023)
app.include_router(health.router)

# Article I: Modular Core - Base path info


@app.get("/")
async def root():
    return {"message": "AI Sales Agent Infrastructure is running."}
