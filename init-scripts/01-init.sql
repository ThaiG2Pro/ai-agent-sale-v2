-- init-scripts/01-init.sql
-- Runs only during initial database initialization (when volume is empty)
-- Ensures pgvector extension is enabled and sets recommended pgvector setting

CREATE EXTENSION IF NOT EXISTS vector;
ALTER SYSTEM SET hnsw.ef_search = 64;

-- Note: ALTER SYSTEM requires a server restart to apply; this script
-- is suitable for fresh installs. For existing databases, run the ALTER
-- command manually and restart Postgres.
