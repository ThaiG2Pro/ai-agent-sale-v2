"""AsyncPostgresSaver factory using psycopg3 (separate from asyncpg).
Security: JsonPlusSerializer(pickle_fallback=False) prevents CVE-2026-27794.
"""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg_pool import AsyncConnectionPool


async def create_checkpointer(dsn: str) -> AsyncPostgresSaver:
    # Step 1: Run setup() using a direct autocommit connection (required because
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block).
    async with AsyncPostgresSaver.from_conn_string(dsn) as setup_saver:
        await setup_saver.setup()

    # Step 2: Build the pool-based saver for all runtime operations.
    pool = AsyncConnectionPool(conninfo=dsn, max_size=10, open=False)
    await pool.open()
    return AsyncPostgresSaver(pool, serde=JsonPlusSerializer(pickle_fallback=False))
