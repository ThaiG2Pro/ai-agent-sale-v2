"""Root test configuration — redirects ALL tests to the isolated test database.

Why this exists:
    Tests must never touch development/production data.  This file ensures
    every engine, session factory, and Alembic migration target uses the
    dedicated ``ai_agent_test`` database.

How it works:
    1. ``os.environ["DB_NAME"]`` is overridden at *module load time* — before
       any application module is imported.  pydantic-settings reads ``DB_NAME``
       when ``Settings()`` is first instantiated (triggered by an import of
       ``core.config``), so forcing the env var here guarantees that every
       ``settings.database_url`` call throughout the test session returns the
       test-DB URL.

    2. The session-scoped ``_setup_test_database`` fixture (autouse) runs once
       per ``pytest`` invocation.  It creates the ``ai_agent_test`` database if
       absent, enables the ``pgvector`` extension, and applies all Alembic
       migrations so the schema is always up to date before any test runs.

    3. Per-test cleanup remains the responsibility of individual test fixtures
       (the ``db_session`` fixtures in ``test_rag.py`` and
       ``test_search_latency.py`` already DELETE their rows at teardown).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

# ── MUST BE FIRST — before any application import ───────────────────────────
# pydantic-settings gives environment variables higher priority than .env
# files, so setting DB_NAME here overrides whatever is in `.env` and ensures
# every Settings() instantiation (and therefore every engine URL) points at
# the test database for the entire test session.
os.environ["DB_NAME"] = "ai_agent_test"
# ────────────────────────────────────────────────────────────────────────────

import pytest


@pytest.fixture(scope="session", autouse=True)
def _setup_test_database() -> None:  # type: ignore[return]
    """Create ``ai_agent_test`` + run Alembic migrations (once per session).

    This sync fixture is intentionally *not* async so it can call
    ``asyncio.run()`` freely without conflicting with pytest-asyncio's
    per-function event-loop management.
    """
    # Import after env override so Settings() picks up DB_NAME=ai_agent_test
    from core.config import settings

    # ── Step 1: ensure the test database exists ──────────────────────────────
    async def _maybe_create_db() -> None:
        import asyncpg  # transitive dep via asyncpg in pyproject.toml

        admin_dsn = (
            f"postgresql://{settings.DB_USER}:{settings.DB_PASSWORD}"
            f"@{settings.DB_HOST}:{settings.DB_PORT}/postgres"
        )
        conn = await asyncpg.connect(admin_dsn, timeout=15)
        try:
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1",
                settings.DB_NAME,
            )
            if not exists:
                # CREATE DATABASE cannot run inside a transaction block.
                await conn.execute(f'CREATE DATABASE "{settings.DB_NAME}"')
                print(f"\n[conftest] Created test database: {settings.DB_NAME}")

                # Enable pgvector on the freshly created database.
                test_dsn = admin_dsn.replace("/postgres", f"/{settings.DB_NAME}")
                test_conn = await asyncpg.connect(test_dsn, timeout=15)
                try:
                    await test_conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                finally:
                    await test_conn.close()
            else:
                # DB exists — ensure pgvector is present (idempotent).
                test_dsn = admin_dsn.replace("/postgres", f"/{settings.DB_NAME}")
                test_conn = await asyncpg.connect(test_dsn, timeout=15)
                try:
                    await test_conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                finally:
                    await test_conn.close()
        finally:
            await conn.close()

    asyncio.run(_maybe_create_db())

    # ── Step 2: apply Alembic migrations via subprocess ──────────────────────
    # Running in a subprocess completely isolates the asyncio.run() call
    # inside alembic/env.py from pytest-asyncio's event-loop machinery.
    # The child process inherits os.environ (including DB_NAME=ai_agent_test),
    # so migrations target the test database automatically.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Alembic migration failed on test database "
            f"'{os.environ['DB_NAME']}':\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )

    print(f"[conftest] Migrations applied to '{settings.DB_NAME}' ✓")
