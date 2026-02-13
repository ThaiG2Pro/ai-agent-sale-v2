# Tasks: Core System Foundation & Infrastructure

**Feature**: Core System Foundation & Infrastructure
**Plan**: `specs/001-project-infra-setup/plan.md`

## Phase 1: Setup (Project Initialization)

Story goal: Initialize the project environment and base infrastructure.

- [X] T001 Initialize project with `uv init` and Python 3.13+ in `pyproject.toml`
- [X] T002 [P] Configure `ruff` linting and formatting rules in `pyproject.toml`
- [X] T003 [P] Create `docker-compose.yml` with PostgreSQL 17 and `pgvector`
- [X] T004 [P] Create `.env.example` with mandatory infra variables (DB, Admin Key, Ollama)

## Phase 2: Foundational (Blocking Prerequisites)

Story goal: Establish core architectural components (DB, Config, Logging).

- [X] T005 [P] Configure Pydantic Settings for environment variables in `core/config.py`
- [X] T006 [P] Setup SQLAlchemy async engine and session factory in `services/database.py`
- [X] T007 Define SQLAlchemy models in `models/schema.py` (Product, TextEmbedding, Conversation, SemanticCache) with `agent_v1` schema
- [X] T008 Initialize Alembic and create initial migration to create `agent_v1` schema and tables in `migrations/`
- [X] T009 [P] Configure Logfire for structured JSON logging to Stdout in `core/logging.py`
- [X] T010 [P] Implement async exception handling and middleware in `api/middleware.py`
- [X] T011 Initialize FastAPI app with Logfire and database session management in `api/main.py`

## Phase 3: [US1] Project Environment Readiness

Story goal: Standardized environment setup and quality enforcement.

**Independent Test**: `uv sync` completes successfully and `ruff check` passes.

- [ ] T012 [US1] Implement `uv sync --dry-run` verification check in `pyproject.toml`
- [ ] T013 [P] [US1] Add pre-commit hook or script for ruff check/format in `scripts/lint.sh`

## Phase 4: [US2] High-Performance Data Foundation

Story goal: Implement storage, vector search, and semantic caching.

**Independent Test**: RAG CLI can ingest and search data; semantic cache returns hits for identical queries.

- [ ] T014 [US2] Implement SHA256 canonicalization for queries in `services/semantic_cache.py`
- [ ] T015 [US2] Implement L1 (Exact Match) cache lookup in `services/semantic_cache.py`
- [ ] T016 [US2] Implement L2 (Vector Similarity) cache lookup using `pgvector` in `services/semantic_cache.py`
- [ ] T017 [US2] Create RAG administration CLI in `cli/rag_admin.py` with ingestion and search commands
- [ ] T018 [US2] Secure admin endpoints with `X-Admin-Key` header check in `api/dependencies.py`
- [ ] T019 [P] [US2] Implement `/admin/rag/ingest` endpoint in `api/routes/admin.py`
- [ ] T020 [P] [US2] Implement `/admin/rag/search` endpoint in `api/routes/admin.py`
- [ ] T021 [US2] Write integration tests for RAG search and cache in `tests/integration/test_rag.py`

## Phase 5: [US3] Responsive Service Monitoring

Story goal: Implement health monitoring with strict performance targets.

**Independent Test**: `/health` returns 200 OK with DB status in < 10ms.

- [ ] T022 [US3] Implement `/health` endpoint with DB connectivity check in `api/routes/health.py`
- [ ] T023 [P] [US3] Add health router to `api/main.py`
- [ ] T024 [US3] Write TDD unit test for health endpoint in `tests/unit/test_health.py`

## Phase 6: [US4] Zero-Cost Local Intelligence

Story goal: AI functions running locally via LiteLLM + Ollama.

**Independent Test**: System generates responses and embeddings without internet connection.

- [ ] T025 [US4] Configure LiteLLM for local Ollama in `core/ai_config.py`
- [ ] T026 [US4] Implement `AIGateway` with async wrappers for LiteLLM calls in `services/ai.py`
- [ ] T027 [US4] Implement fallback logic to cloud provider in `services/ai.py`
- [ ] T028 [US4] Implement model switching latency check in `services/ai.py`
- [ ] T029 [US4] Write integration test for offline generation in `tests/integration/test_ai_offline.py`

## Final Phase: Polish & Cross-Cutting Concerns

Story goal: Final documentation and architectural records.

- [ ] T030 [P] Write ADR 001 for technical selections in `docs/adr/001_tech_selection.md`
- [ ] T031 Document minimum hardware requirements in `README.md`
- [ ] T032 [P] Finalize `pyproject.toml` metadata and descriptions
- [ ] T033 Implement version-based cache invalidation logic in `services/semantic_cache.py`
- [ ] T034 Create `tests/eval/gold_dataset.json` with sample queries and expected responses
- [ ] T035 Implement Tier 1 evaluation runner script in `scripts/tier1_eval.py`
- [ ] T036 Add performance verification for health endpoint under 1 req/s load in `tests/integration/test_health_load.py`
- [ ] T037 Add benchmarking for search latency at 10k entries in `tests/integration/test_search_latency.py`
- [ ] T038 Update data-model.md with citation fields (source_chunk_ids)

## Dependencies

- Phase 2 depends on completion of Phase 1
- Phase 3, 4, 5, 6 depend on completion of Phase 2
- Phase 4 (L2 Cache) depends on `AIGateway` from Phase 6 if embeddings are generated on-the-fly

## Parallel Execution Examples

- T002, T003, T004 (Setup)
- T005, T006, T009, T010 (Foundational Infrastructure)
- US1 (Linting) can run parallel to US3 (Monitoring) once api/main.py (T011) exists

## Implementation Strategy

- **Incremental Core**: Build the DB and Config layers first (Phase 2).
- **Early Value**: Implement Health check (US3) to verify the stack immediately.
- **RAG Foundation**: Implement the CLI and basic vector storage (US2) before the AI generation (US4).
