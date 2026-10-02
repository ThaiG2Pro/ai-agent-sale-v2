# ADR 001: Core Technical Stack Selection

## Status
Accepted (2026-02) — item 5 amended by [ADR-006](006-model-provider-and-embedding-runtime.md)

> **Terminology.** "Article N" references point to the project's original engineering
> charter (`docs/specs/*/plan.md`, "Constitution"): II = graph-based orchestration & simplicity,
> V = async I/O only, VI = all model calls through LiteLLM, VII = single database, X = cost /
> image-size discipline. They are kept for traceability to the spec-kit era.

## Context
The AI Sales Agent project requires a foundation that is high-performance, asynchronous, and capable of running entirely offline to adhere to the "Zero-Cost-First" philosophy. We need to select tools for environment management, database interaction, AI model gateway, and observability.

## Decision
We have selected the following technical stack:

1.  **Environment Management**: `uv` for Python 3.13+. It provides fast, reproducible environments and dependency resolution.
2.  **Database**: PostgreSQL 17 with `pgvector` 0.8+. PostgreSQL 17 offers optimized memory for vector operations.
3.  **Database Driver**: `asyncpg` via `SQLAlchemy 2.0` (Async). Mandated by Article V for non-blocking I/O.
4.  **Migrations**: `Alembic`. Standard for versioned schema changes in the SQLAlchemy ecosystem.
5.  **AI Gateway**: `LiteLLM` as the single model seam. Initially fronting a local `Ollama` endpoint for 100% offline development (0 VND baseline); the backend choice per deployment context (Ollama / llama.cpp / vLLM / cloud) and the in-process fastembed embedding runtime are decided in ADR-006.
6.  **Observability**: `OpenTelemetry` (OTLP gateway) with a protocol-first approach. Local development uses a self-hosted Arize Phoenix (offline-first) for tracing and evaluation; production/staging may forward OTLP to services like Logfire or LangSmith/Langfuse when needed. Stdout/JSON logging remains a fallback.
7.  **Configuration**: `pydantic-settings` for environment variable management via `.env` files.

## Consequences
- **Reproducibility**: All developers will use the exact same versions of dependencies.
- **Performance**: The system will handle heavy I/O loads (LLM/DB) without blocking the main event loop.
- **Cost**: Development and initial testing can occur with zero API costs.
- **Scale**: The stack is capable of scaling from local dev to cloud production with minimal configuration changes.

## Alternatives Considered
- **Poetry**: Rejected for being slower than `uv`.
- **Redis**: Rejected to maintain a "Single-DB" architecture and reduce infra complexity for SMEs.
- **Standard Logging**: Rejected in favor of structured JSON logging for better observability.
