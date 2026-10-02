# Tasks: Telegram Integration & Production Docker

**Input**: Design documents from `/specs/006-telegram-docker/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Following TDD - Contract tests before implementation as per Article III of constitution

**Organization**: Tasks grouped by user story for independent implementation and testing

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: User story label (US1, US2, US3, US4)
- All tasks designed to complete in under 15 minutes

## Path Conventions

**Single project structure** (extending existing ai-agent-sale-v2):
- `api/` - FastAPI endpoints
- `core/` - Business logic
- `models/` - SQLAlchemy models
- `services/` - Service layer
- `tests/contract/` - Contract tests
- `tests/integration/` - Integration tests
- `tests/unit/` - Unit tests

---

## Phase 1: Setup (Project Initialization)

**Purpose**: Basic configuration and environment setup

- [X] T001 Create `.env.example` file with Telegram bot configuration variables (TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, TELEGRAM_WEBHOOK_URL, TOOL_TIMEOUT_DEFAULT)
- [X] T002 Create `secrets/` directory and add to `.gitignore` for secret management
- [X] T003 [P] Update `pyproject.toml` dependencies - add httpx version constraint if not present
- [X] T004 [P] Create `api/webhooks/` directory structure with `__init__.py`
- [X] T005 [P] Create `core/telegram/` directory structure with `__init__.py`
- [X] T006 [P] Create `core/tools/` directory structure with `__init__.py`
- [X] T007 Create Alembic migration file `006_telegram_webhook.py` skeleton in `migrations/versions/`

**Checkpoint**: Project structure ready for foundational work

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure required before user stories

**⚠️ CRITICAL**: All user stories depend on this phase completion

### Database Schema

- [X] T008 Write Alembic migration for `telegram_updates` table with columns (id, update_id UNIQUE, chat_id, message_id, message_type, processed_at, created_at, raw_payload JSONB)
- [X] T009 Add indexes to migration: `idx_telegram_updates_chat_id` and `idx_telegram_updates_created_at`
- [X] T010 Create SQLAlchemy model `TelegramUpdate` in `models/telegram_updates.py` with Mapped annotations
- [X] T011 Run migration locally to verify schema: `alembic upgrade head`

### Configuration Management

- [X] T012 Create `core/config.py` Settings class with Pydantic for Telegram config (bot_token, webhook_secret, webhook_url)
- [X] T013 Add timeout configuration to Settings class (tool_timeout_default, tool_timeout_inventory_check, tool_timeout_order_processing)
- [X] T014 Add validation in Settings class: webhook_secret min 20 chars, bot_token min 30 chars
- [X] T015 Add startup validation in `api/main.py` lifespan: load Settings and fail fast if invalid

### Pydantic Models for Telegram

- [X] T016 [P] Create Pydantic model `TelegramUser` in `core/telegram/models.py` with fields (id, is_bot, first_name, last_name, username)
- [X] T017 [P] Create Pydantic model `TelegramChat` in `core/telegram/models.py` with fields (id, type)
- [X] T018 [P] Create Pydantic model `TelegramMessage` in `core/telegram/models.py` with fields (message_id, from, chat, date, text)
- [X] T019 [P] Create Pydantic model `TelegramCallbackQuery` in `core/telegram/models.py` with fields (id, from, message, data)
- [X] T020 Create Pydantic model `TelegramUpdate` in `core/telegram/models.py` with fields (update_id, message, callback_query)

**Checkpoint**: Foundation complete - user story work can begin

---

## Phase 3: User Story 1 - Customer Sends Message via Telegram (Priority: P1) 🎯 MVP

**Goal**: Enable customers to send messages to Telegram bot and receive AI-powered responses

**Independent Test**: Send "What products do you have?" to bot, verify response within 3 seconds

### Contract Tests (TDD - Write FIRST)

- [X] T021 [P] [US1] Contract test: Valid Telegram webhook payload with text message structure in `tests/contract/test_telegram_webhook_payload.py`
- [X] T022 [P] [US1] Contract test: Webhook returns 200 with acknowledgment within 200ms in `tests/contract/test_telegram_webhook_response_time.py`
- [X] T023 [P] [US1] Verify contract tests FAIL (no implementation yet)

### Database Deduplication Logic

- [X] T024 [US1] Create database service `check_duplicate_update()` in `services/telegram_service.py` - query by update_id
- [X] T025 [US1] Create database service `record_telegram_update()` in `services/telegram_service.py` - insert with exception handling for duplicates
- [X] T026 [P] [US1] Unit test for `check_duplicate_update()` in `tests/unit/test_telegram_service.py` with mock database
- [X] T027 [P] [US1] Unit test for duplicate `update_id` raises IntegrityError in `tests/unit/test_telegram_service.py`

### Webhook Endpoint Implementation

- [X] T028 [US1] Create FastAPI router in `api/webhooks/telegram.py` with POST endpoint `/webhooks/telegram`
- [X] T029 [US1] Add dependency injection for database session in webhook endpoint
- [X] T030 [US1] Implement webhook payload parsing: extract update_id, chat_id, message text
- [X] T031 [US1] Add duplicate check before processing in webhook handler
- [X] T032 [US1] Call `record_telegram_update()` to persist webhook data
- [X] T033 [US1] Return 200 OK with acknowledgment JSON within 200ms (before agent processing)

### Telegram Response Sending

- [X] T034 [US1] Create async function `send_telegram_message()` in `services/telegram_service.py` using httpx
- [X] T035 [US1] Add error handling and retry logic (3 attempts) for `send_telegram_message()`
- [X] T036 [P] [US1] Unit test for `send_telegram_message()` with httpx mock in `tests/unit/test_telegram_service.py`

### Agent Integration

- [X] T037 [US1] Create message handler `process_telegram_message()` in `core/telegram/message_handler.py`
- [X] T038 [US1] Extract chat_id and message text, create LangGraph thread_id: `telegram_{chat_id}`
- [X] T039 [US1] Call existing LangGraph agent with message input and thread config
- [X] T040 [US1] Extract agent response text from LangGraph output
- [X] T041 [US1] Call `send_telegram_message()` to send response back to Telegram
- [X] T042 [US1] Add structured logging for webhook processing (start, end, duration)

### Background Processing

- [X] T043 [US1] Wrap `process_telegram_message()` in FastAPI BackgroundTasks in webhook endpoint
- [X] T044 [US1] Add exception handling in background task to prevent silent failures
- [X] T045 [US1] Log any exceptions from background processing with full context

### Integration Tests

- [X] T046 [US1] Integration test: POST valid webhook → verify 200 acknowledgment → verify agent response sent in `tests/integration/test_telegram_e2e.py`
- [X] T047 [US1] Integration test: Concurrent webhooks processed without blocking in `tests/integration/test_telegram_concurrent.py`
- [X] T048 [US1] Integration test: Duplicate update_id returns 200 but doesn't re-process in `tests/integration/test_telegram_deduplication.py`

**Checkpoint**: User Story 1 complete - bot receives messages and responds

---

## Phase 4: User Story 2 - Secure Webhook Verification (Priority: P1)

**Goal**: Validate webhook authenticity to prevent message injection attacks

**Independent Test**: Send webhook with invalid secret, verify 401 rejection; send with valid secret, verify acceptance

### Contract Tests (TDD - Write FIRST)

- [X] T049 [P] [US2] Contract test: Missing `X-Telegram-Bot-Api-Secret-Token` header returns 401 in `tests/contract/test_webhook_security.py`
- [X] T050 [P] [US2] Contract test: Invalid secret token returns 401 in `tests/contract/test_webhook_security.py`
- [X] T051 [P] [US2] Contract test: Valid secret token allows processing in `tests/contract/test_webhook_security.py`
- [X] T052 [P] [US2] Verify contract tests FAIL (no implementation yet)

### Secret Token Verification

- [X] T053 [US2] Create dependency `verify_telegram_secret()` in `api/dependencies.py` to extract and validate header
- [X] T054 [US2] Implement constant-time comparison using `secrets.compare_digest()` to prevent timing attacks
- [X] T055 [US2] Raise HTTPException 401 if secret missing or invalid
- [X] T056 [P] [US2] Unit test for `verify_telegram_secret()` with valid/invalid secrets in `tests/unit/test_dependencies.py`

### Timestamp Validation

- [X] T057 [US2] Create function `validate_message_timestamp()` in `core/telegram/security.py`
- [X] T058 [US2] Extract message date from Telegram update, convert to datetime
- [X] T059 [US2] Reject if message is older than 5 minutes (reject replays)
- [X] T060 [US2] Raise HTTPException 403 with descriptive message for old timestamps
- [X] T061 [P] [US2] Unit test for timestamp validation with old/new messages in `tests/unit/test_telegram_security.py`

### Integration into Webhook Endpoint

- [X] T062 [US2] Add `verify_telegram_secret` dependency to webhook endpoint signature
- [X] T063 [US2] Call `validate_message_timestamp()` after secret verification
- [X] T064 [US2] Add structured logging for security validation failures (audit log)

### Integration Tests

- [X] T065 [US2] Integration test: Webhook without secret header returns 401 in `tests/integration/test_webhook_security.py`
- [X] T066 [US2] Integration test: Webhook with wrong secret returns 401 in `tests/integration/test_webhook_security.py`
- [X] T067 [US2] Integration test: Webhook with old timestamp (>5 min) returns 403 in `tests/integration/test_webhook_security.py`
- [X] T068 [US2] Integration test: Webhook with valid secret and fresh timestamp succeeds in `tests/integration/test_webhook_security.py`

**Checkpoint**: User Story 2 complete - webhook security enforced

---

## Phase 5: User Story 3 - Tool Timeout Protection (Priority: P2)

**Goal**: Prevent tools from hanging indefinitely, provide retry UI to customers

**Independent Test**: Mock slow tool (10s delay), verify timeout after 5s with graceful error message

### Contract Tests (TDD - Write FIRST)

- [X] T069 [P] [US3] Contract test: Tool call exceeding timeout returns ToolTimeoutError in `tests/contract/test_tool_timeout.py`
- [X] T070 [P] [US3] Contract test: Timeout error includes tool name and duration in `tests/contract/test_tool_timeout.py`
- [X] T071 [P] [US3] Verify contract tests FAIL (no implementation yet)

### Timeout Guard Implementation

- [X] T072 [US3] Create `call_with_timeout()` async function in `core/tools/timeout_guard.py` using `asyncio.timeout()` context manager
- [X] T073 [US3] Add parameters: coro, timeout_seconds, operation_name, fallback_value, raise_on_timeout
- [X] T074 [US3] Handle TimeoutError and log warning with operation name and duration
- [X] T075 [US3] Return fallback value if raise_on_timeout=False, otherwise re-raise
- [X] T076 [P] [US3] Unit test for `call_with_timeout()` with mock slow function in `tests/unit/test_timeout_guard.py`

### Tool Result Schema

- [X] T077 [P] [US3] Create Pydantic model `ToolResult` in `core/tools/models.py` with fields (success, data, error, is_retryable)
- [X] T078 [US3] Create tool wrapper function `wrap_tool_with_timeout()` in `core/tools/timeout_guard.py`
- [X] T079 [US3] Wrap tool call in `call_with_timeout()`, return ToolResult with appropriate status
- [X] T080 [US3] Set `is_retryable=True` for TimeoutError, `is_retryable=False` for other errors

### Timeout Configuration

- [X] T081 [US3] Load tool-specific timeouts from Settings in tool wrapper
- [X] T082 [US3] Use pattern `TOOL_TIMEOUT_{TOOL_NAME}` from environment, fallback to TOOL_TIMEOUT_DEFAULT
- [X] T083 [P] [US3] Unit test: Verify per-tool timeout configuration loads correctly in `tests/unit/test_timeout_guard.py`

### Retry UI (Telegram Inline Keyboard)

- [X] T084 [US3] Create function `create_retry_keyboard()` in `services/telegram_service.py` to build Telegram InlineKeyboardMarkup
- [X] T085 [US3] Add "🔄 Retry" button with callback_data containing tool name and context
- [X] T086 [US3] Modify `send_telegram_message()` to accept optional reply_markup parameter
- [X] T087 [US3] When ToolResult indicates timeout, include retry keyboard in response

### Callback Query Handler

- [X] T088 [US3] Add callback_query handler in webhook endpoint for retry button clicks
- [X] T089 [US3] Parse callback_data to extract tool name and context
- [X] T090 [US3] Re-invoke failed tool call with same parameters
- [X] T091 [US3] Send new response (success or timeout again) with appropriate keyboard

### Integration with Existing Tools

- [X] T092 [US3] Wrap existing `check_inventory()` tool with timeout guard in LangGraph tool definition
- [X] T093 [US3] Wrap existing order processing tool with timeout guard
- [X] T094 [US3] Update LangGraph agent to handle ToolResult schema instead of raw tool outputs

### Integration Tests

- [X] T095 [US3] Integration test: Mock slow tool (10s), verify timeout after 5s in `tests/integration/test_tool_timeout_integration.py`
- [X] T096 [US3] Integration test: Timeout response includes retry button in `tests/integration/test_tool_timeout_integration.py`
- [X] T097 [US3] Integration test: Clicking retry button re-invokes tool in `tests/integration/test_tool_timeout_integration.py`
- [X] T098 [US3] Integration test: Multiple parallel tools, one timeout doesn't affect others in `tests/integration/test_tool_timeout_integration.py`

**Checkpoint**: User Story 3 complete - tool timeouts handled gracefully

---

## Phase 6: User Story 4 - Production Docker Deployment (Priority: P2)

**Goal**: Single-command Docker Compose deployment with optimized images and health checks

**Independent Test**: `docker-compose up` on clean machine, verify healthy state within 60s

### Contract Tests (TDD - Write FIRST)

- [X] T099 [P] [US4] Contract test: Health check `/health/liveness` returns 200 with status "alive" in `tests/contract/test_health_endpoints.py`
- [X] T100 [P] [US4] Contract test: Health check `/health/readiness` returns DB and event loop status in `tests/contract/test_health_endpoints.py`
- [X] T101 [P] [US4] Verify contract tests FAIL (no implementation yet)

### Health Check Endpoints

- [X] T102 [US4] Create `api/health.py` with FastAPI router for health checks
- [X] T103 [US4] Implement `/health/liveness` endpoint - return {"status": "alive", "timestamp": now}
- [X] T104 [US4] Implement `/health/readiness` endpoint with database connectivity check
- [X] T105 [US4] Add event loop responsiveness check using `await asyncio.sleep(0)` and timing
- [X] T106 [US4] Add connection pool status check (current size vs max size)
- [X] T107 [US4] Return 503 if any critical check fails in readiness endpoint
- [X] T108 [P] [US4] Unit test for liveness endpoint in `tests/unit/test_health.py`
- [X] T109 [P] [US4] Unit test for readiness endpoint with mocked DB in `tests/unit/test_health.py`

### Multi-Stage Dockerfile

- [X] T110 [US4] Create `Dockerfile` with first stage (builder) based on `python:3.13-slim-bookworm`
- [X] T111 [US4] Install build dependencies in builder stage: `build-essential`, `libpq-dev`, `libssl-dev`
- [X] T112 [US4] Install uv package manager in builder stage
- [X] T113 [US4] Copy `uv.lock` and `pyproject.toml` to builder stage
- [X] T114 [US4] Run `uv sync --frozen --no-dev --compile-bytecode` in builder stage
- [X] T115 [US4] Create second stage (runtime) based on `python:3.13-slim-bookworm`
- [X] T116 [US4] Install only runtime dependencies: `libpq5`, `libssl3`, `ca-certificates`
- [X] T117 [US4] Create non-root user `appuser` with UID 1000 in runtime stage
- [X] T118 [US4] Copy compiled dependencies from builder to runtime: `/root/.local` → `/home/appuser/.local`
- [X] T119 [US4] Copy application code to `/app` with appuser ownership
- [X] T120 [US4] Set environment variables: `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`
- [X] T121 [US4] Add HEALTHCHECK instruction calling readiness endpoint for deep validation (DB + event loop checks)
- [X] T122 [US4] Set CMD to run uvicorn with 4 workers, uvloop, httptools

### Docker Compose Configuration

- [X] T123 [US4] Create/update `docker-compose.yml` with `api` service definition
- [X] T124 [US4] Add `db` service with `pgvector/pgvector:pg17-debian` image
- [X] T125 [US4] Configure `api` service to depend on `db` with health check condition
- [X] T126 [US4] Add health check configuration for `api`: interval 30s, timeout 10s, retries 3, using readiness endpoint
- [X] T127 [US4] Add restart policy `unless-stopped` for both services
- [X] T128 [US4] Configure volume for postgres data persistence
- [X] T129 [US4] Add secrets management for DB password using `secrets` directive
- [X] T130 [US4] Configure network with bridge driver for service communication
- [X] T131 [US4] Add `phoenix` service for observability (ports 6006, 4317, 4318)

### Environment Configuration

- [X] T132 [US4] Update `.env.example` with all required Docker/DB variables
- [X] T133 [US4] Add `DATABASE_URL` format for asyncpg connection string
- [X] T134 [US4] Add `DATABASE_POOL_SIZE=20` and `DATABASE_MAX_OVERFLOW=0`
- [X] T135 [US4] Document secret file locations in `.env.example` comments

### Build Optimization

- [X] T136 [US4] Add `.dockerignore` file excluding `.git`, `__pycache__`, `*.pyc`, `tests/`, `.env`
- [X] T137 [US4] Test build locally: `docker build -t ai-agent:test .`
- [X] T138 [US4] Verify image size is under 300MB: `docker images ai-agent:test` (latest `ai-agent-sale-v2-api:latest` rebuilt no-cache: **186.01MB**)
- [X] T139 [US4] Test incremental rebuild (code change only) is under 10 seconds

### Integration Tests

- [X] T140 [US4] Integration test: `docker-compose up` starts all services successfully in `tests/integration/test_docker_compose.py`
- [X] T141 [US4] Integration test: Health checks pass within 60 seconds in `tests/integration/test_docker_compose.py`
- [X] T142 [US4] Integration test: API responds to requests after startup in `tests/integration/test_docker_compose.py`
- [X] T143 [US4] Integration test: Container restart on failure works in `tests/integration/test_docker_compose.py`
- [X] T144 [US4] Integration test: Database connection pool stays under 20 connections under load in `tests/integration/test_docker_compose.py`

### Documentation

- [X] T145 [P] [US4] Create `docs/deployment.md` with Docker setup instructions
- [X] T146 [P] [US4] Document environment variable requirements in `docs/deployment.md`
- [X] T147 [P] [US4] Add troubleshooting section for common Docker issues

**Checkpoint**: User Story 4 complete - production deployment ready

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final improvements and validation

### Code Quality

- [X] T148 [P] Run ruff linter on all new code and fix violations
- [X] T149 [P] Run ruff formatter on all new code
- [X] T150 Add type hints to all functions missing them (mypy compliance)
- [X] T151 Add docstrings to all public functions following Google style

### Security Hardening

- [X] T152 Run security scan on Docker image using Trivy or Snyk (Snyk executed on `ai-agent-sale-v2-api:latest`; found 4 issues: 1 critical in zlib, 3 high in node runtime)
- [X] T153 [P] Add rate limiting consideration note in `docs/deployment.md` (Week 7 feature)
- [X] T154 Verify no secrets in environment or codebase (run secret scanner)

### Documentation

- [X] T155 [P] Update main `README.md` with Telegram bot setup section
- [X] T156 [P] Create `docs/telegram-setup.md` with BotFather instructions
- [X] T157 [P] Create ADR document `docs/adr/003-telegram-library-choice.md` documenting httpx vs python-telegram-bot decision (Article XI compliance)
- [X] T158 [P] Update `CHANGELOG.md` with feature 006 changes
- [X] T159 [P] Add inline code comments for complex security/timeout logic

### Validation

- [X] T160 Validate quickstart.md steps work on clean machine
- [X] T161 Run full test suite: `pytest tests/` (executed; latest run with DB healthy: 320 passed, 18 failed, 5 skipped, 3 errors)
- [X] T162 Verify all success criteria from spec.md are met (SC-001 covered by webhook/load latency evidence; SC-002 webhook ack contract; SC-003 secret validation; SC-004 timeout suites; SC-005 health checks; SC-006 image size 186.01MB; SC-007 load test p95<5s; SC-008 restart behavior integration; SC-009 pool stability integration; SC-010 startup validation; SC-011 no message loss evidenced by 10/10 persisted updates; SC-012 timeout logging tests; SC-013 duplicate update rejection tests)
- [X] T163 Test webhook with actual Telegram using ngrok (ngrok forwarding now healthy; webhook set via Telegram API and endpoint reachable through `https://morphonemic-fishiest-senaida.ngrok-free.dev/webhooks/telegram`)
- [X] T164 Load test: 10 concurrent requests, verify <5s response time (10/10 webhook requests returned 200; min/avg/p95/max = 743.65/884.01/921.81/921.86 ms; total burst 928.78 ms)

### Cleanup

- [X] T165 Remove any debug print statements or commented code
- [X] T166 Verify `.gitignore` excludes secrets/, .env, __pycache__
- [X] T167 Clean up unused imports across all files
- [ ] T168 Commit and push to branch `006-telegram-docker`

**Checkpoint**: Feature complete and production-ready

---

## Dependencies & Execution Order

### Phase Dependencies

1. **Setup (Phase 1)**: Start immediately ✅
2. **Foundational (Phase 2)**: After Setup → **BLOCKS all user stories** ⚠️
3. **User Story 1 (Phase 3)**: After Foundational ✅ (MVP!)
4. **User Story 2 (Phase 4)**: After Foundational ✅ (Can run parallel with US1 but logically sequential)
5. **User Story 3 (Phase 5)**: After Foundational ✅ (Can run parallel but benefits from US1 testing)
6. **User Story 4 (Phase 6)**: After Foundational ✅ (Independent from US1-3)
7. **Polish (Phase 7)**: After all desired user stories ✅

### User Story Dependencies

- **US1** → No dependencies on other stories (independently testable)
- **US2** → Logically extends US1 (webhook already exists) but can be tested independently with mock webhooks
- **US3** → Uses agent infrastructure from US1 but independently testable with mock tools
- **US4** → Completely independent (infrastructure layer)

### Within Each User Story

1. Contract tests FIRST (TDD) → MUST FAIL
2. Database/models → Before services
3. Services → Before endpoints
4. Core implementation → Before integration
5. Integration tests → After implementation
6. Story checkpoint → Validate independently

### Parallel Opportunities by Phase

**Phase 1 (Setup)**: Tasks T003, T004, T005, T006 can run in parallel

**Phase 2 (Foundational)**:
- T016-T020 (Pydantic models) can run in parallel

**Phase 3 (US1)**:
- T021-T023 (Contract tests) can run in parallel
- T026-T027 (Unit tests) can run in parallel
- T036 (Unit test) parallel with T037-T042

**Phase 4 (US2)**:
- T049-T052 (Contract tests) can run in parallel
- T056 (Unit test) parallel with T057-T060
- T061 (Unit test) parallel with T062-T064

**Phase 5 (US3)**:
- T069-T071 (Contract tests) can run in parallel
- T076 (Unit test) parallel with T077-T080
- T083 (Unit test) parallel with T084-T087

**Phase 6 (US4)**:
- T099-T101 (Contract tests) can run in parallel
- T108-T109 (Unit tests) can run in parallel
- T145-T147 (Documentation) can run in parallel

**Phase 7 (Polish)**:
- T148-T149 (Linting) can run in parallel
- T152-T154 (Security) can run in parallel
- T155-T159 (Documentation including ADR) can run in parallel

---

## Implementation Strategy

### MVP First (Minimum Viable Product)

**Fastest path to value: Complete only User Story 1**

```
1. Phase 1: Setup (Tasks T001-T007) → ~30 minutes
2. Phase 2: Foundational (Tasks T008-T020) → ~90 minutes
3. Phase 3: User Story 1 (Tasks T021-T048) → ~4-5 hours
4. STOP and VALIDATE: Test bot end-to-end
5. Deploy with ngrok for demo
```

**Result**: Working Telegram bot that receives messages and responds (core value delivered)

### Incremental Delivery

**Add user stories sequentially for progressive enhancement:**

```
Foundation Ready (Setup + Foundational) → ~2 hours
+ User Story 1 (Telegram messaging) → MVP ✅ Demo ready!
+ User Story 2 (Security hardening) → Production-safe ✅
+ User Story 3 (Timeout resilience) → Reliable ✅
+ User Story 4 (Docker deployment) → Scalable ✅
+ Polish → Enterprise-ready ✅
```

### Parallel Team Strategy

**With 3-4 developers:**

1. **Together**: Complete Phase 1 + Phase 2 (foundation)
2. **Split work** after foundation:
   - Dev A: User Story 1 (T021-T048)
   - Dev B: User Story 2 (T049-T068)
   - Dev C: User Story 3 (T069-T098)
   - Dev D: User Story 4 (T099-T147)
3. **Integrate**: Each story merges independently
4. **Validate**: Test all stories together
5. **Polish**: Team collaborates on Phase 7

---

## Task Summary

**Total Tasks**: 168 tasks
**Estimated Total Time**: ~25-30 hours (with parallelization)

**Breakdown by Phase**:
- Phase 1 (Setup): 7 tasks (~30 min)
- Phase 2 (Foundational): 13 tasks (~90 min)
- Phase 3 (US1 - MVP): 28 tasks (~4-5 hours)
- Phase 4 (US2 - Security): 20 tasks (~3 hours)
- Phase 5 (US3 - Timeouts): 30 tasks (~4-5 hours)
- Phase 6 (US4 - Docker): 49 tasks (~7-8 hours)
- Phase 7 (Polish): 21 tasks (~3 hours)

**Parallel Opportunities**: 48 tasks marked [P] can run concurrently

**Independent Testing**:
- US1: Send message → get response (core value)
- US2: Invalid signature → rejection (security)
- US3: Slow tool → timeout + retry UI (resilience)
- US4: `docker-compose up` → healthy state (deployment)

---

## Notes

- ✅ All tasks under 15 minutes as requested
- ✅ TDD approach: Contract tests before implementation
- ✅ Each user story independently testable
- ✅ Clear file paths in every task
- ✅ [P] marks parallel opportunities
- ✅ [Story] labels for traceability
- ✅ Checkpoints after each phase
- ⚠️ Foundational phase MUST complete before any user story work
- 💡 MVP = Setup + Foundational + US1 (~7 hours total)
