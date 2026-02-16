"""Why this exists: Provides non-blocking database connectivity and session management.
What it does: Initializes SQLAlchemy AsyncEngine and async_sessionmaker.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings

# Article V: Asynchronous I/O Mandate - Using asyncpg
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
)

# Article VII: Externalized State - Stateless session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Dependency for obtaining an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
