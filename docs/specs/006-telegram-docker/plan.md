# Implementation Plan: Telegram Integration & Production Docker

**Branch**: `006-telegram-docker` | **Date**: 2026-03-30 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/006-telegram-docker/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Implement an async Telegram bot webhook integration with security verification, tool timeout protection, and production-ready Docker deployment. The system will enable customers to interact with the AI sales agent through Telegram while maintaining low latency (<3s response), high security (100% signature verification), and SME-friendly deployment constraints (<300MB image, <20 DB connections). Key features include duplicate message prevention, graceful timeout handling with retry UI, and comprehensive health checks for container orchestration.

## Technical Context

**Language/Version**: Python 3.13+  
**Primary Dependencies**: FastAPI (async), python-telegram-bot or httpx (for Telegram API), Docker 24+, Docker Compose v2  
**Storage**: PostgreSQL 17 with unique constraint on `telegram_updates.update_id` for deduplication  
**Testing**: pytest (async fixtures), pytest-asyncio, httpx for webhook endpoint testing  
**Target Platform**: Linux server (Docker containers)  
**Project Type**: Single project (extend existing FastAPI application)  
**Performance Goals**: 
- Webhook acknowledgment: <200ms
- End-to-end response: <3s for 95% of requests
- Docker image build: <5 minutes
- Container startup: <30s to healthy state

**Constraints**: 
- Docker base image: <300MB
- Database connection pool: max 20 connections
- Tool timeout: 5s default (configurable per tool)
- Webhook signature validation: mandatory (no bypass)
- Environment-based configuration only (no hardcoded secrets)

**Scale/Scope**: 
- Support 10+ concurrent conversations
- Handle Telegram retry storms gracefully (idempotency)
- Multi-stage Docker build with separate build/runtime layers
- Health check probes for orchestration

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Article I: Modular Core & Testability
- ✅ **PASS**: Telegram webhook handler will be in `api/webhooks/telegram.py` (API layer)
- ✅ **PASS**: Core business logic (message processing, agent invocation) remains in `services/` and `core/`
- ✅ **PASS**: Tool timeout wrapper will be in `core/tools/timeout_guard.py` (reusable core logic)
- ✅ **PASS**: No CLI required for this feature (Telegram integration is an API endpoint, not a pipeline)

### Article II: Simplicity and Anti-Abstraction
- ✅ **PASS**: Single project extension (no new projects)
- ✅ **PASS**: Using FastAPI's native dependency injection, no custom Repository patterns
- ✅ **PASS**: LangGraph orchestration (explicitly permitted by constitution)
- ✅ **PASS**: Direct asyncpg for DB operations (no ORM lazy loading)

### Article III: Deterministic TDD & AI Evaluation
- ✅ **PASS**: Webhook signature verification → TDD (deterministic)
- ✅ **PASS**: Timeout guard mechanism → TDD (deterministic)
- ✅ **PASS**: Docker health check endpoint → TDD (deterministic)
- ✅ **PASS**: Update ID deduplication → TDD (deterministic)
- ⚠️ **AWARE**: Agent message processing is non-deterministic but covered by existing evaluation framework (not in scope for this feature)

### Article IV: Integration-First Testing
- ✅ **PASS**: Contract tests for Telegram webhook payload validation (before implementation)
- ✅ **PASS**: Integration tests with real PostgreSQL for deduplication
- ✅ **PASS**: Docker Compose integration test (full stack startup)
- ✅ **PASS**: Health check integration test (database + event loop verification)

### Article V: Asynchronous I/O Mandate
- ✅ **PASS**: All webhook handlers use async/await
- ✅ **PASS**: Database queries via asyncpg
- ✅ **PASS**: Telegram API calls via httpx (async HTTP client)
- ✅ **PASS**: Tool timeout implemented using asyncio.wait_for()
- ✅ **PASS**: No blocking operations in event loop

### Article VI: Structured Determinism
- ✅ **PASS**: Telegram Update payloads validated via Pydantic models
- ✅ **PASS**: Tool timeout responses structured (TimeoutError with metadata)
- ✅ **PASS**: Health check responses use typed schemas
- ✅ **PASS**: No regex parsing of webhook data

### Article VII: Stateless Runtime, Persistent Memory
- ✅ **PASS**: Docker containers are stateless (can restart anytime)
- ✅ **PASS**: Conversation state persisted via LangGraph PostgresSaver (existing)
- ✅ **PASS**: Update ID tracking in database (survives restarts)
- ✅ **PASS**: No in-memory state required for this feature

### Article VIII: The Human Circuit Breaker
- ⚠️ **AWARE**: This feature doesn't introduce new Critical Actions
- ✅ **PASS**: Existing HITL mechanism (Week 4) remains enforced
- ✅ **PASS**: Tool timeout doesn't bypass HITL gates

### Article IX: Data Provenance & Hallucination Zero
- ⚠️ **AWARE**: This feature is infrastructure (doesn't change RAG behavior)
- ✅ **PASS**: Existing RAG citation requirements remain intact
- ✅ **PASS**: Timeout errors don't bypass confidence thresholds

### Article X: The Frugal Architect
- ✅ **PASS**: No additional LLM calls introduced by this feature
- ✅ **PASS**: Telegram updates processed efficiently (no token waste)
- ✅ **PASS**: Docker multi-stage build reduces image size (cost optimization)
- ✅ **PASS**: Connection pooling (max 20) prevents resource waste

### Article XI: Documentation as Code
- ✅ **PASS**: quickstart.md will document deployment process
- ✅ **PASS**: Docstrings required for webhook handler and timeout guard
- ⚠️ **TODO**: Create ADR for Telegram library choice (python-telegram-bot vs httpx direct)

### Article XII: The Efficiency Metric
- ⚠️ **AWARE**: This feature doesn't affect model escalation logic
- ✅ **PASS**: Timeout guards prevent wasted compute on stuck operations
- ✅ **PASS**: Zero-cost baseline maintained (Telegram bot works with local Ollama)

**GATE STATUS**: ✅ **PASS** - All applicable articles satisfied. No constitutional violations. Proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/006-telegram-docker/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (being generated)
├── data-model.md        # Phase 1 output (will be generated)
├── quickstart.md        # Phase 1 output (will be generated)
├── contracts/           # Phase 1 output (will be generated)
│   ├── telegram-webhook.openapi.yaml
│   └── health-check.openapi.yaml
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
api/
├── webhooks/
│   ├── __init__.py
│   └── telegram.py          # NEW: Telegram webhook endpoint
├── health.py                # NEW: Health check endpoints for Docker
└── dependencies.py          # Existing: May need timeout config injection

core/
├── tools/
│   └── timeout_guard.py     # NEW: Async timeout wrapper for tool calls
└── telegram/
    ├── __init__.py
    └── message_handler.py   # NEW: Business logic for Telegram message processing

models/
└── telegram_updates.py      # NEW: SQLAlchemy model for update_id deduplication

services/
└── telegram_service.py      # NEW: Service layer for Telegram interactions

tests/
├── contract/
│   ├── test_telegram_webhook_contract.py    # NEW: Webhook payload validation
│   └── test_tool_timeout_contract.py        # NEW: Timeout behavior contract
├── integration/
│   ├── test_telegram_webhook_integration.py # NEW: End-to-end webhook flow
│   ├── test_docker_health_checks.py         # NEW: Health check integration
│   └── test_update_deduplication.py         # NEW: Database deduplication
└── unit/
    └── test_timeout_guard.py                # NEW: Timeout guard unit tests

# Docker files (repository root)
Dockerfile                   # MODIFIED: Multi-stage build optimization
docker-compose.yml           # MODIFIED: Add health checks, restart policies
.env.example                 # MODIFIED: Add Telegram bot token, webhook secret
```

**Structure Decision**: Extending existing single-project structure. Telegram integration adds new API endpoints (`api/webhooks/`), reusable core logic (`core/tools/timeout_guard.py`, `core/telegram/`), and database models (`models/telegram_updates.py`). Docker configuration files at repository root. No new projects needed.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No constitutional violations detected. This section is intentionally left empty as all design decisions comply with the project constitution.
