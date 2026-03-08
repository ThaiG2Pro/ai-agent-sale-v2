"""AsyncPostgresSaver factory using psycopg3 (separate from asyncpg).
Security: JsonPlusSerializer(pickle_fallback=False) prevents CVE-2026-27794.
"""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg_pool import AsyncConnectionPool


async def create_checkpointer(dsn: str) -> AsyncPostgresSaver:
    pool = AsyncConnectionPool(conninfo=dsn, max_size=10, open=False)
    await pool.open()
    saver = AsyncPostgresSaver(pool, serde=JsonPlusSerializer(pickle_fallback=False))
    await saver.setup()  # Creates 4 LangGraph tables if not exists
    return saver
