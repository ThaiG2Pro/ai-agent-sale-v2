# Implementation Plan: Core System Foundation & Infrastructure

**Branch**: `001-project-infra-setup` | **Date**: 2026-02-13 | **Spec**: `/specs/001-project-infra-setup/spec.md`
**Input**: Feature specification from `/specs/001-project-infra-setup/spec.md`

## Summary

This feature establishes the core foundation of the AI Sales Agent system. It focuses on setting up a high-performance, asynchronous environment using `uv`, containerizing the PostgreSQL 17 database with `pgvector`, and implementing the initial FastAPI structure. The goal is a "Zero-Cost-First" architecture that operates offline using LiteLLM and local Ollama, while ensuring all I/O is non-blocking and state is externalized to Postgres. Key foundations include Data Provenance (citations in RAG responses) and Tier 1 Evaluation (automated quality checks).

## Technical Context

**Language/Version**: Python 3.13+  
**Primary Dependencies**: FastAPI, LiteLLM, Ollama, SQLAlchemy 2.0 (async), Alembic, Pydantic, OpenTelemetry (OTLP), ruff  
**Storage**: PostgreSQL 17 + pgvector 0.8+ (organized in a dedicated schema `agent_v1`)  
**Testing**: pytest (Deterministic TDD)  
**Target Platform**: Linux (Docker)  
**Project Type**: Single project  
**Performance Goals**: API `/health` response < 10ms, Semantic Search latency < 5ms  
**Constraints**: 100% offline-capable (0 VND baseline), Strict async I/O (asyncpg), Stateless runtime, Tier 1 evaluation for RAG quality, Citation provenance in responses  
**Scale/Scope**: Foundations for a scalable SME AI agent, handling product knowledge and session-based interactions with data provenance, evaluation, and performance verification.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Article | Gate | Status |
|---------|------|--------|
| I | Business logic decoupled from API (services/core)? | [x] |
| I | RAG CLI present for ingestion/debugging? | [x] |
| II | Simplicity: No unnecessary abstraction (Repository, etc.)? | [x] |
| III | TDD for deterministic components (Health check, DB utils)? | [x] |
| V | Async-only drivers (asyncpg, httpx)? | [x] |
| VII | Stateless runtime (external state in Postgres)? | [x] |
| X | Zero-Cost Baseline supported (Ollama)? | [x] |
| XII | Efficiency Metric: 0 VND Functional Offline? | [x] |

## Project Structure

### Documentation (this feature)

```text
specs/001-project-infra-setup/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
ai-agent-sale-v2/
├── api/                # API layer (FastAPI)
│   ├── routes/
│   └── main.py
├── services/           # Business logic (Storage, AI Gateway, Cache)
│   ├── database.py
│   ├── ai.py
│   └── semantic_cache.py
├── cli/                # RAG Administration
│   └── rag_admin.py
├── models/             # SQLAlchemy Schema definitions
│   └── schema.py
├── migrations/         # Alembic migrations
├── tests/              # Test hierarchy
│   ├── integration/
│   └── unit/
├── docker-compose.yml
└── pyproject.toml
```

**Structure Decision**: Single project layout with clear separation between `api/`, `services/`, and `models/` as mandated by Article I.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
