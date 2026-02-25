# Research: Core System Foundation & Infrastructure

## DEC-001: Environment & Dependency Management
- **Decision**: Use `uv` for Python 3.13+.
- **Rationale**: `uv` provides lightning-fast dependency resolution and a robust lockfile. It aligns with the "Lean" philosophy.
- **Alternatives considered**: Poetry (slower), pip (no built-in lock management).

## DEC-002: Asynchronous Database Driver
- **Decision**: `SQLAlchemy 2.0` with `asyncpg`.
- **Rationale**: `asyncpg` is the high-performance async driver for PostgreSQL. SQLAlchemy 2.0 provides type-safe schema definitions while allowing non-blocking I/O (Article V).
- **Alternatives considered**: `psycopg2` (blocking), `psycopg3` (async supported but `asyncpg` is often faster for raw throughput).

## DEC-003: Database Migrations
- **Decision**: `Alembic`.
- **Rationale**: Industry standard for SQLAlchemy. Allows versioned, reproducible schema changes.
- **Alternatives considered**: Manual SQL scripts (prone to error), `SQLModel` automatic sync (too risky for production).

## DEC-004: AI Model Gateway
- **Decision**: `LiteLLM` with local `Ollama` endpoint.
- **Rationale**: LiteLLM provides a unified interface for 100+ models. Ollama allows 100% offline development (0 VND baseline).
- **Alternatives considered**: Direct OpenAI SDK (vendor lock-in, costs), custom Ollama wrapper (redundant effort).

## DEC-005: Semantic Cache Strategy
- **Decision**: PostgreSQL-backed cache using `SHA256` for L1 (Exact Match) and `pgvector` HNSW for L2 (Semantic Match).
- **Rationale**: Article X mandates aggressive caching. Using Postgres for both allows us to keep the architecture lean (no Redis).
- **Alternatives considered**: Redis (violates "No Redis" rule), in-memory (not persistent across restarts).

## DEC-006: Observability & Logging

- **Decision**: `OpenTelemetry` (OTLP gateway) with protocol-first tracing and metrics. Configure OTLP locally to send traces/metrics to a self-hosted Arize Phoenix for offline-first debugging and evaluation. In Production/Staging OTLP may be forwarded to services such as Logfire (cloud) or LangSmith/Langfuse  for deeper Python and LangGraph traces. Stdout/JSON logging remains a required fallback.
- **Rationale**: Standardized telemetry via OTLP enables flexible backend routing and vendor-agnostic observability while preserving Twelve-Factor App principles.
- **Alternatives considered**: Local files (requires rotation/persistence management), database logging (high overhead).

## DEC-007: Administrative Authentication
- **Decision**: Environment-variable-backed API Key (`X-Admin-Key`).
- **Rationale**: Simple, effective for CLI-to-API and internal admin tasks without the complexity of OAuth2/JWT for early infra.
- **Alternatives considered**: None (too risky), Localhost-only (restricts remote administration if needed later).

## DEC-008: Configuration Management
- **Decision**: Environment variables managed via `.env` files and `pydantic-settings`.
- **Rationale**: Follows Twelve-Factor App principles and Article VI (Type Safety).
- **Alternatives considered**: YAML/JSON config files (harder to manage secrets securely).
