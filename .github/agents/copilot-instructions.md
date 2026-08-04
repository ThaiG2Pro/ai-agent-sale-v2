# ai-agent-sale-v2 Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-02-26

## Active Technologies
- Python 3.13+ + LangGraph (StateGraph + Command API), LiteLLM Router, Pydantic v2, anyio, pytest-asyncio, respx (003-agentic-workflow)
- PostgreSQL 17 + pgvector 0.8+ (schema `agent_v1`) — `model_trace` table (already migrated), `MemorySaver` for dev checkpointing (003-agentic-workflow)
- Python 3.13+ + LangGraph 0.3+, LiteLLM (Router, latest), FastAPI (Async), SQLAlchemy 2.0 (asyncpg), Pydantic v2, logfire, pytest-asyncio, respx (003-agentic-workflow)
- PostgreSQL 17 + pgvector 0.8+, schema `agent_v1` (existing). `model_traces` table already exists. No new tables required for Week 3. (003-agentic-workflow)
- Python 3.13+ + LangGraph ≥ 0.3, FastAPI (async), SQLAlchemy 2.0 (async), asyncpg, LiteLLM, Pydantic v2, langgraph-checkpoint-postgres (004-human-in-loop-hitl)
- PostgreSQL 17 + pgvector 0.8 (schema: `agent_v1`). LangGraph checkpointer tables: `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`. New HITL tables: `hitl_metadata`, `review_actions`, `confidence_scores`, `queued_messages`, `support_queue`. (004-human-in-loop-hitl)
- Python 3.13+ + LangGraph ≥ 0.3, `langgraph-checkpoint-postgres` (AsyncPostgresSaver), (004-human-in-loop-hitl)
- PostgreSQL 17 + pgvector 0.8+ (schema `agent_v1`); 4 HITL application tables (004-human-in-loop-hitl)
- Python 3.13+ + FastAPI (async), LangGraph + AsyncPostgresSaver (already wired in W4), SQLAlchemy 2.0 async + asyncpg, LiteLLM (LIGHT_CHAT_MODEL for summarization), pgvector 0.8+ (HNSW already in use), Pydantic v2 (005-async-persistence-memory)
- PostgreSQL 17 — `agent_v1` schema (single-DB, no Redis) (005-async-persistence-memory)
- Python 3.13+ + FastAPI (async), python-telegram-bot or httpx (for Telegram API), Docker 24+, Docker Compose v2 (006-telegram-docker)
- PostgreSQL 17 with unique constraint on `telegram_updates.update_id` for deduplication (006-telegram-docker)

- Python 3.13+ + FastAPI, LiteLLM, SQLAlchemy 2.0 (async), asyncpg, (002-vietnamese-rag-eval)

## Project Structure

```text
src/
tests/
```

## Commands

cd src [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] pytest [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] ruff check .

## Code Style

Python 3.13+: Follow standard conventions

## Recent Changes
- 006-telegram-docker: Added Python 3.13+ + FastAPI (async), python-telegram-bot or httpx (for Telegram API), Docker 24+, Docker Compose v2
- 005-async-persistence-memory: Added Python 3.13+ + FastAPI (async), LangGraph + AsyncPostgresSaver (already wired in W4), SQLAlchemy 2.0 async + asyncpg, LiteLLM (LIGHT_CHAT_MODEL for summarization), pgvector 0.8+ (HNSW already in use), Pydantic v2
- 004-human-in-loop-hitl: Added Python 3.13+ + LangGraph ≥ 0.3, `langgraph-checkpoint-postgres` (AsyncPostgresSaver),


<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
