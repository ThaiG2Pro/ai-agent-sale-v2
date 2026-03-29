# Tasks: Async Persistence & Memory

**Input**: Design documents from `specs/005-async-persistence-memory/`  
**Branch**: `005-async-persistence-memory`  
**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Data Model**: [data-model.md](./data-model.md)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no shared dependencies)
- **[USn]**: Which user story this task serves
- Tasks without `[P]` must complete in listed order within their phase
- No task should exceed 15 minutes

---

## Phase 1: Setup (Config, ADR, Test Skeletons)

**Purpose**: Establish all new configuration, documentation, and empty test files before any implementation begins. These tasks are prerequisites and most can run in parallel.

- [ ] T001 Add 8 memory config settings to `core/config.py`: `MEMORY_SUMMARY_THRESHOLD=20`, `MEMORY_RELEVANCE_THRESHOLD=0.75`, `MEMORY_TOP_K=3`, `CHECKPOINT_SIZE_WARN_BYTES=1_048_576`, `CHECKPOINT_RETENTION_DAYS=90`, `MEMORY_MERGE_PLATFORMS=True`, `DB_POOL_SIZE=20`, `INTENT_LOCK_MAX_RETRIES=3`, `INTENT_LOCK_RETRY_BACKOFF_MS=[50, 100, 200]`
- [ ] T002 [P] Create `services/memory/__init__.py` with module-level docstring explaining Week 5 memory layer purpose (Article XI)
- [ ] T003 [P] Create `docs/adr/ADR-005-memory-hnsw-embedding-governance.md` with Context → Decision (m=16, ef_construction=64, model_version composite key) → Consequences → Alternatives Considered sections
- [ ] T004 [P] Create `tests/unit/test_intent_extractor.py` with file docstring, imports (`pytest`, `pytest_asyncio`, `unittest.mock`), and empty test class `TestSalesIntentExtractor`
- [ ] T005 [P] Create `tests/unit/test_intent_tracker.py` with file docstring, imports, and empty test class `TestIntentTracker`
- [ ] T006 [P] Create `tests/unit/test_summarizer.py` with file docstring, imports, and empty test class `TestConversationSummarizer`
- [ ] T007 [P] Create `tests/unit/test_semantic_memory.py` with file docstring, imports, and empty test class `TestSemanticMemoryService`
- [ ] T008 [P] Create `tests/unit/test_background_tasks.py` with file docstring, imports, and empty test class `TestPostTurnTasks`
- [ ] T009 [P] Create `tests/integration/test_memory_flow.py` with `@pytest.mark.integration` marker, file docstring, and empty test class `TestMemoryFlow`
- [ ] T010 [P] Create `tests/contract/test_memory_api.py` with `TestClient` import, `X-Admin-Key` header fixture, and empty `TestMemoryContractPreImpl` class

**Checkpoint**: Config and test scaffolding in place. All test files importable (`pytest --collect-only` passes with 0 errors).

---

## Phase 2: Foundational (ORM Models, Pydantic, State, Migration)

**Purpose**: All new database tables, Pydantic models, and state extensions. Everything downstream depends on this phase.

⚠️ **CRITICAL**: No user story implementation can begin until T011–T030 are complete.

### 2a. Enums and Pydantic Models

- [ ] T011 Add `UrgencyLevel` StrEnum (`LOW`, `MEDIUM`, `HIGH`, `UNKNOWN`) to `core/agent/state.py` after existing enums
- [ ] T012 Add `FOLLOW_UP = "FOLLOW_UP"` and `OTHER = "OTHER"` values to `IntentEnum` in `core/agent/state.py` (SMALLTALK already exists; will add to skip set in T013)
- [ ] T013 Add `SKIP_INTENT_EXTRACTION: frozenset[IntentEnum]` constant to `core/agent/state.py` containing `{FOLLOW_UP, OTHER, SMALLTALK}`
- [ ] T014 [P] Add `ConversationSummaryOutput` Pydantic model to `core/agent/state.py` with fields: `products_discussed: list[str]`, `customer_preference: str | None`, `budget_stated: str | None`, `open_questions: list[str]`, `summary_model: str` — all with defaults
- [ ] T015 [P] Add `SalesIntentExtraction` Pydantic model to `core/agent/state.py` with fields: `budget_range: str | None`, `urgency_level: UrgencyLevel = UNKNOWN`, `product_interest: list[str]`, `decision_timeline: str | None`, `contact_preference: str | None` — all nullable/defaulted, `ConfigDict(strict=True)`

### 2b. AgentState Extension

- [ ] T016 Add 5 new fields to `AgentState` TypedDict in `core/agent/state.py`: `customer_id: str`, `memory_context: list[dict]`, `memory_retrieval_scores: list[float]`, `thread_summary_exists: bool`, `sales_intent_skipped: bool`
- [ ] T017 Add `customer_id: str` parameter (required) to `make_initial_state()` in `core/agent/state.py`
- [ ] T018 Add default values for the 5 new fields in `make_initial_state()` body: `customer_id=customer_id`, `memory_context=[]`, `memory_retrieval_scores=[]`, `thread_summary_exists=False`, `sales_intent_skipped=False`
- [ ] T019 Write test in `tests/unit/test_agent_state.py`: `make_initial_state()` raises `TypeError` when `customer_id` is not provided
- [ ] T020 Write test in `tests/unit/test_agent_state.py`: `make_initial_state()` returns state with all 5 new fields correctly set to defaults

### 2c. ORM Models

- [ ] T021 Add `EmbeddingStatus` string enum (`ACTIVE`, `STALE`) to `models/schema.py`
- [ ] T022 Add `IntentStatus` string enum (`NEW`, `ENGAGED`, `AWAITING_QUOTE`, `CONTACTED`, `CONVERTED`, `LOST`) to `models/schema.py`
- [ ] T023 [P] Add `ConversationSummary` ORM model to `models/schema.py` with all fields per data-model.md: `id`, `session_id`, `customer_id`, `products_discussed` (JSONB), `customer_preference`, `budget_stated`, `open_questions` (JSONB), `summary_model`, `turn_count_at_summary`, `created_at`; add `Index("idx_conv_summary_session_id", "session_id")`
- [ ] T024 [P] Add `SemanticMemory` ORM model to `models/schema.py` with all fields: `id`, `customer_id`, `session_id`, `summary_id` (FK → conversation_summaries with CASCADE), `embedding` (`Vector(settings.EMBED_DIMENSION)`), `embedding_model`, `model_version`, `status` (default ACTIVE), `created_at`; add compound `Index("idx_semantic_memory_customer_status", "customer_id", "status")`
- [ ] T025 [P] Add HNSW index on `SemanticMemory.embedding` in `models/schema.py`: `postgresql_using="hnsw"`, `postgresql_with={"m": 16, "ef_construction": 64}`, `postgresql_ops={"embedding": "vector_cosine_ops"}`
- [ ] T026 [P] Add `SalesIntentLog` ORM model to `models/schema.py` (append-only audit table) with all fields per data-model.md; add `Index("idx_sales_intent_log_customer", "customer_id", "created_at")`
- [ ] T027 [P] Add `IntentTracking` ORM model to `models/schema.py` with all fields including `version: Mapped[int] = mapped_column(default=1)`, `status_changed_at`, `status_change_trigger`; add `UniqueConstraint("customer_id")` and `Index("idx_intent_tracking_urgency_status", "urgency_level", "intent_status")`

### 2d. Alembic Migration

- [ ] T028 Generate Alembic migration: `uv run alembic revision --autogenerate -m "add_memory_tables"` — review generated file in `migrations/versions/`
- [ ] T029 Verify migration SQL contains `CREATE INDEX ... USING hnsw` for `semantic_memory.embedding` — fix if autogenerate missed it (manual addition to migration)
- [ ] T030 Add downgrade migration in `migrations/versions/XXXX_add_memory_tables.py`: `DROP TABLE` in reverse order (intent_tracking → sales_intent_log → semantic_memory → conversation_summaries)

**Checkpoint**: Run `uv run alembic upgrade head` — all 4 tables exist. Run `uv run alembic downgrade -1` — all 4 dropped cleanly.

---

## Phase 3: User Story 4 — Checkpoint Durability & Restart Resilience (Priority: P1)

**Goal**: Server restart loses zero active conversation state. Pending HITL survives crash. Checkpoint size is monitored. Graph schema mismatches surface visibly.

**Independent Test**: Kill `uvicorn`, restart, call `GET /hitl/session/{id}/state` — same state returned.

- [ ] T031 [US4] Add `GRAPH_SCHEMA_VERSION = "005"` constant to `core/agent/graph.py` (module-level, above `build_graph`)
- [ ] T032 [US4] Wrap `graph.aget_state()` call in `services/hitl/service.py` with `try/except (KeyError, ValidationError, TypeError)` — on mismatch call `_mark_incompatible(session_id)` helper that logs error with `checkpoint_id` and `GRAPH_SCHEMA_VERSION`
- [ ] T033 [US4] Implement `_mark_incompatible(session_id, error)` helper in `services/hitl/service.py`: logs `ERROR` with `session_id`, error type, `GRAPH_SCHEMA_VERSION`; updates `HITLMetadata.status = "INCOMPATIBLE"` if paused entry exists (FR-018: status must be INCOMPATIBLE, not abandoned)
- [ ] T034 [US4] Test: `_mark_incompatible()` sets `HITLMetadata.status = "INCOMPATIBLE"` when paused entry found (FR-018 compliance)
- [ ] T035 [US4] Write test in `tests/unit/test_hitl_service.py`: deserialization `KeyError` in `aget_state()` → INCOMPATIBLE logged, never re-raised to caller
- [ ] T036 [US4] Implement `_check_checkpoint_size(session_id, db)` async helper in `services/memory/background.py`: query checkpoint `payload` column (BYTEA/JSONB) size in bytes, log `WARNING` if `> settings.CHECKPOINT_SIZE_WARN_BYTES` (FR-001b: size warning for large payloads)
- [ ] T037 [US4] Write test in `tests/unit/test_background_tasks.py`: `_check_checkpoint_size()` — mock checkpoint row at 1.5MB → `WARNING` log emitted
- [ ] T038 [US4] Write test in `tests/unit/test_background_tasks.py`: `_check_checkpoint_size()` — mock checkpoint row at 500KB → no `WARNING` log
- [ ] T039 [US4] Write test in `tests/unit/test_background_tasks.py`: `_check_checkpoint_size()` — session_id not found in DB → no exception raised (graceful no-op)
- [ ] T040 [US4] Add `cleanup_old_checkpoints(db)` async function to `cli/rag_admin.py` and `delete-customer` CLI subcommand: query checkpoints older than `CHECKPOINT_RETENTION_DAYS` WHERE `status NOT IN ('paused')` in `hitl_metadata` — log count of eligible rows (dry-run by default)
- [ ] T041 [US4] Write test in `tests/unit/test_background_tasks.py`: cleanup query excludes any `session_id` that has `HITLMetadata.status = "paused"` — assert paused sessions never in result set
- [ ] T042 [US4] Write integration test in `tests/integration/test_memory_flow.py`: `test_checkpoint_survives_restart` — create session, send message, simulate restart via fresh `build_graph()` call with same DB, verify `graph.aget_state(config)` returns same state values
- [ ] T042b [US4] Add `discard-checkpoint` CLI subcommand to `cli/rag_admin.py`: takes `session_id`, marks checkpoint as INCOMPATIBLE (if currently INCOMPATIBLE) and discards it from DB (FR-018: operator manual discard path)
- [ ] T042c [US4] Add `migrate-checkpoint` CLI subcommand to `cli/rag_admin.py`: takes `session_id`, logs detailed checkpoint schema mismatch diagnostics including expected vs actual field counts for operator-led recovery (FR-018: operator migration guidance path)
- [ ] T042d [US4] Write test: `discard-checkpoint` with INCOMPATIBLE session → checkpoint removed, session marked as abandoned
- [ ] T042e [US4] Write test: `migrate-checkpoint` with mismatched schema → logs diagnostics without modification (dry-run safe)

---

## Phase 4: User Story 2 — Sales Intent Extraction & Tracking (Priority: P1)

**Goal**: Every signal-bearing turn extracts and persists structured sales intent. Admin can query the lead list filtered by urgency and status.

**Independent Test**: Send PRICING message → call `GET /memory/intent/{customer_id}` → `urgency_level`, `budget_range`, `product_interest` correctly extracted.

### 4a. Contract Tests (Red Phase — must fail before implementation)

- [ ] T043 [P] [US2] Write contract test in `tests/contract/test_memory_api.py`: `GET /memory/intent/unknown-customer` → `404` (pre-implementation gate)
- [ ] T044 [P] [US2] Write contract test: `GET /memory/intents?urgency_level=HIGH` → `401` without admin key (auth gate)
- [ ] T045 [P] [US2] Write contract test: `PATCH /memory/intent/unknown-customer/status` body `{"new_status": "CONTACTED", "expected_version": 1}` → `404` (pre-implementation gate)

### 4b. Intent Extractor Service

- [ ] T046 [US2] Create `services/memory/intent_extractor.py` with class `SalesIntentExtractor` and docstring explaining signal-gating logic (FR-011)
- [ ] T047 [US2] Implement `SalesIntentExtractor.should_extract(primary_intent: IntentEnum) -> bool` — returns `False` if `primary_intent in SKIP_INTENT_EXTRACTION`, else `True`
- [ ] T048 [US2] Write test in `tests/unit/test_intent_extractor.py`: `should_extract(IntentEnum.FOLLOW_UP)` returns `False`
- [ ] T049 [US2] Write test: `should_extract(IntentEnum.SMALLTALK)` returns `False`
- [ ] T050 [US2] Write test: `should_extract(IntentEnum.OTHER)` returns `False`
- [ ] T051 [US2] Write test: `should_extract(IntentEnum.PRICING)` returns `True`
- [ ] T052 [US2] Write test: `should_extract(IntentEnum.COMPLAINT)` returns `True`
- [ ] T053 [US2] Implement `SalesIntentExtractor.extract(conversation_text: str, db: AsyncSession) -> SalesIntentExtraction` in `services/memory/intent_extractor.py` — LiteLLM call with `model=settings.LIGHT_CHAT_MODEL`, `response_format=SalesIntentExtraction`
- [ ] T054 [US2] Write test in `tests/unit/test_intent_extractor.py`: `extract()` with mock LiteLLM returning no signals → `urgency_level=UNKNOWN`, all other fields `None` or empty list (FR-013: no hallucination)
- [ ] T055 [US2] Write test: `extract()` with mock response containing "cần gấp" → `urgency_level=HIGH`
- [ ] T056 [US2] Write test: `extract()` with mock response containing "khoảng 20 triệu" → `budget_range` not None
- [ ] T057 [US2] Write test: `extract()` asserts `model=settings.LIGHT_CHAT_MODEL` in LiteLLM call kwargs (Article XII efficiency metric)

### 4c. Intent Tracker Service

- [ ] T058 [US2] Create `services/memory/intent_tracker.py` with class `IntentTracker` and docstring explaining optimistic lock strategy (FR-015b)
- [ ] T059 [US2] Implement `IntentTracker.upsert_with_lock(customer_id, session_id, extraction: SalesIntentExtraction, db: AsyncSession)` — INSERT ON CONFLICT DO UPDATE with `version+1`, `status_change_trigger="agent"`
- [ ] T060 [US2] Implement version conflict retry loop in `upsert_with_lock()`: max `INTENT_LOCK_MAX_RETRIES` (default 3), backoff delays from `INTENT_LOCK_RETRY_BACKOFF_MS` (default [50, 100, 200]); raise `IntentLockConflictError` after max failures
- [ ] T061 [US2] Write test in `tests/unit/test_intent_tracker.py`: `upsert_with_lock()` creates new row when customer does not exist — verify `version=1`
- [ ] T062 [US2] Write test: `upsert_with_lock()` updates existing row → `version` increments from 1 to 2
- [ ] T063 [US2] Write test: `upsert_with_lock()` — mock DB raises rowcount=0 twice then succeeds on third try → succeeds, correct `version` in result
- [ ] T064 [US2] Write test: `upsert_with_lock()` — mock DB raises rowcount=0 three times → `IntentLockConflictError` raised
- [ ] T065 [US2] Write test: concurrent `asyncio.gather` of 2 `upsert_with_lock()` calls for same `customer_id` on real DB → `version` ends at 3, no row duplicated, no `IntegrityError` (R6 race condition test)
- [ ] T066 [US2] Implement `IntentTracker.update_status(customer_id, new_status, expected_version, trigger, db)` — UPDATE with version check, record `status_changed_at=now()`, `status_change_trigger=trigger`
- [ ] T067 [US2] Write test: `update_status()` with correct `expected_version` → status updated, `status_changed_at` is set, `status_change_trigger="admin"`
- [ ] T068 [US2] Write test: `update_status()` with stale `expected_version` → raises `OptimisticLockError` (HTTP 409)
- [ ] T069 [US2] Write test: `update_status()` with non-existent `customer_id` → raises `CustomerNotFoundError` (HTTP 404)

### 4d. Memory Admin Routes

- [ ] T070 [P] [US2] Implement `GET /memory/intent/{customer_id}` in `api/routes/memory.py` — query `IntentTracking` by `customer_id`, return `IntentTrackingResponse` schema, raise 404 if missing
- [ ] T071 [P] [US2] Implement `GET /memory/intents` in `api/routes/memory.py` — filter by `urgency_level`, `intent_status`; ORDER BY urgency priority DESC, `last_updated` DESC; paginate with `limit`/`offset`
- [ ] T072 [P] [US2] Implement `PATCH /memory/intent/{customer_id}/status` in `api/routes/memory.py` — call `IntentTracker.update_status(trigger="admin")`; map `OptimisticLockError` → 409, `CustomerNotFoundError` → 404
- [ ] T073 [US2] Mount memory router in `api/main.py` with prefix `/memory` and `tags=["memory"]`
- [ ] T074 [US2] Write integration test `test_intent_extraction_full_flow` in `tests/integration/test_memory_flow.py`: send PRICING message via chat endpoint → `GET /memory/intent/{customer_id}` → assert `urgency_level`, `product_interest` populated

---

## Phase 5: User Story 1 — Background Dispatch & Memory Context Injection (Priority: P1)

**Goal**: All 4 post-turn tasks run as parallel fire-and-forget async background tasks. Customer TTFT is unchanged. Memory context is injected into answer_node.

**Independent Test**: API response returns before background tasks complete. Answer node includes past memory in prompt when non-empty.

### 5a. Background Task Coordinator

- [ ] T075 [US1] Implement `_maybe_extract_intent(customer_id, state, db_factory)` async helper in `services/memory/background.py`: checks `SKIP_INTENT_EXTRACTION`, calls extractor + tracker, logs skip or success
- [ ] T076 [US1] Write test in `tests/unit/test_background_tasks.py`: `_maybe_extract_intent()` — FOLLOW_UP intent → extractor NOT called, `sales_intent_skipped=True` logged
- [ ] T077 [US1] Write test: `_maybe_extract_intent()` — PRICING intent → extractor IS called with correct conversation text
- [ ] T078 [US1] Write test: `_maybe_extract_intent()` — extractor raises exception → exception logged, nothing re-raised
- [ ] T079 [US1] Implement `post_turn_tasks(session_id, customer_id, state, db_factory)` in `services/memory/background.py`: `asyncio.gather(_check_checkpoint_size(), _maybe_extract_intent(), _maybe_summarize(), return_exceptions=True)`; log each exception by task index
- [ ] T080 [US1] Write test: `post_turn_tasks()` — all 4 task functions called once (mock each, assert call count)
- [ ] T081 [US1] Write test: `post_turn_tasks()` — Task 2 raises `Exception` → Tasks 1, 3 still complete (return_exceptions isolation)
- [ ] T082 [US1] Write test: `post_turn_tasks()` — all 4 tasks raise exceptions → no exception propagated to caller, all logged

### 5b. API Layer — `asyncio.create_task` Integration

- [ ] T083 [US1] Add `asyncio.create_task(post_turn_tasks(...))` call in `api/routes/chat.py` (or equivalent) immediately after `graph.ainvoke()` returns, before `return` statement
- [ ] T084 [US1] Write test in `tests/unit/test_background_tasks.py`: API handler returns response while `post_turn_tasks` is still "pending" — mock `post_turn_tasks` with `asyncio.sleep(1)` and assert response arrives in < 100ms
- [ ] T085 [US1] Write test: `asyncio.create_task` is called with correct `session_id` and `customer_id` from state

### 5c. Answer Node — Memory Context Injection

- [ ] T086 [US1] Update `core/agent/nodes/answer.py` to include `memory_context` from state in the system prompt when `len(state["memory_context"]) > 0` — format as "Past context from previous conversations:" block
- [ ] T087 [US1] Write test in `tests/unit/test_answer_node.py`: `answer_node` with non-empty `memory_context` → system prompt contains "Past context" block with summary text
- [ ] T088 [US1] Write test: `answer_node` with empty `memory_context` → system prompt does NOT contain "Past context" block (cold start)
- [ ] T089 [US1] Write test: `answer_node` with `memory_context` containing 2 entries → both entries appear in system prompt
- [ ] T090 [US1] Write integration test `test_restart_session_continues` in `tests/integration/test_memory_flow.py`: send message with budget info → restart `build_graph()` → send follow-up → verify state `messages` contains original message (checkpoint preserved)

---

## Phase 6: User Story 5 — Conversation Summarization (Priority: P2)

**Goal**: Threads with 20+ messages are auto-compressed before LLM calls. Token usage reduced ≥ 30%. Failures are graceful.

**Independent Test**: Insert 22 messages for a session → trigger `_maybe_summarize()` → `conversation_summaries` row created with correct fields.

### 6a. Summarizer Service

- [ ] T091 [US5] Create `services/memory/summarizer.py` with class `ConversationSummarizer` and docstring (FR-004, Article XI)
- [ ] T092 [US5] Implement `ConversationSummarizer.should_summarize(message_count: int, has_existing_summary: bool, messages_since_last_summary: int) -> bool` — returns True if `message_count >= MEMORY_SUMMARY_THRESHOLD` AND no summary exists, OR `messages_since_last_summary >= 10`
- [ ] T093 [US5] Write test in `tests/unit/test_summarizer.py`: `should_summarize(19, False, 19)` → `False`
- [ ] T094 [US5] Write test: `should_summarize(20, False, 20)` → `True` (first summary at threshold)
- [ ] T095 [US5] Write test: `should_summarize(30, True, 9)` → `False` (summary exists, not enough new messages)
- [ ] T096 [US5] Write test: `should_summarize(30, True, 10)` → `True` (re-summarize trigger)
- [ ] T097 [US5] Implement `ConversationSummarizer.summarize(messages: list, session_id: str) -> ConversationSummaryOutput` in `services/memory/summarizer.py` — LiteLLM call with `model=settings.LIGHT_CHAT_MODEL`, `response_format=ConversationSummaryOutput`
- [ ] T098 [US5] Write test: `summarize()` mock LiteLLM → `summary_model` field equals `settings.LIGHT_CHAT_MODEL` in returned output (Article XII: assert economy model used)
- [ ] T099 [US5] Write test: `summarize()` with mock conversation mentioning "máy lạnh" → `products_discussed` contains that product
- [ ] T100 [US5] Write test: `summarize()` with mock conversation where customer asked "giá bao nhiêu?" (unanswered) → `open_questions` not empty
- [ ] T101 [US5] Write test: `summarize()` with empty messages list → raises `ValueError` (guard against empty input)
- [ ] T102 [US5] Implement `ConversationSummarizer.save_summary(summary: ConversationSummaryOutput, session_id: str, customer_id: str, turn_count: int, db: AsyncSession)` — INSERT to `conversation_summaries`
- [ ] T103 [US5] Write test: `save_summary()` — inserted row has correct `session_id`, `customer_id`, `turn_count_at_summary`, `summary_model`
- [ ] T104 [US5] Implement `_maybe_summarize(session_id, customer_id, state, db_factory)` async helper in `services/memory/background.py`: orchestrates `should_summarize` → `summarize` → `save_summary` → chain `_update_semantic_memory`
- [ ] T105 [US5] Write test: `_maybe_summarize()` — message count below threshold → neither `summarize()` nor `save_summary()` called
- [ ] T106 [US5] Write test: `_maybe_summarize()` — LiteLLM raises `ConnectionError` → error logged, function returns `None`, no exception propagated (FR-006 graceful degradation)
- [ ] T107 [US5] Write test: `_maybe_summarize()` — DB INSERT fails → error logged, `_update_semantic_memory` NOT called (no dangling embedding)

### 6b. Answer Node — Context Compression

- [ ] T108 [US5] Update `core/agent/nodes/answer.py`: when `state["thread_summary_exists"]` is `True`, replace old messages in context with summary text + last 5 raw messages (FR-005)
- [ ] T109 [US5] Write test: answer_node with `thread_summary_exists=True` and 25 raw messages → LiteLLM called with ≤ 6 context items (1 summary + 5 recent)
- [ ] T110 [US5] Write test: answer_node with `thread_summary_exists=False` and 5 raw messages → LiteLLM called with all 5 messages (no compression)
- [ ] T111 [US5] Write integration test `test_summary_created_at_threshold` in `tests/integration/test_memory_flow.py`: create session, insert 22 messages, call `_maybe_summarize()` directly, assert 1 row in `conversation_summaries` with `turn_count_at_summary=22`

---

## Phase 7: User Story 3 — Semantic Memory Retrieval (Priority: P2)

**Goal**: Cross-session memory search surfaces relevant past summaries. Returns empty for cold-start customers. Strict `customer_id` isolation prevents leakage.

**Independent Test**: Store summary for customer A → search with related query for customer A → top-3 returned. Search for customer B → empty.

### 7a. Semantic Memory Service

- [ ] T112 [US3] Create `services/memory/semantic_memory.py` with class `SemanticMemoryService` and docstring explaining FR-007–010b
- [ ] T113 [US3] Implement `SemanticMemoryService.store(summary_id, customer_id, session_id, summary_text: str, db: AsyncSession) -> SemanticMemory`: embed `summary_text` via `embed_text()`, build `model_version = f"{EMBED_MODEL}@{EMBED_DIMENSION}"`, INSERT to `semantic_memory` with `status=ACTIVE`
- [ ] T114 [US3] Write test in `tests/unit/test_semantic_memory.py`: `store()` inserts row with `model_version="bge-m3@1024"` (matches config)
- [ ] T115 [US3] Write test: `store()` sets `status=ACTIVE` on insert (never STALE at creation)
- [ ] T116 [US3] Write test: `store()` with mock embed returning wrong dimension → raises `EmbeddingDimensionMismatchError`
- [ ] T117 [US3] Implement `SemanticMemoryService.retrieve(customer_id, query: str, top_k: int, min_score: float, db: AsyncSession) -> list[SemanticMemoryResult]`: embed query, cosine search `WHERE customer_id = :cid AND status = 'ACTIVE' AND model_version = :ver ORDER BY embedding <=> :vec LIMIT top_k`, filter by `min_score` in Python
- [ ] T118 [US3] Write test: `retrieve()` with `customer_id="A"` — mock DB returns 3 rows all belonging to customer A → 3 results returned
- [ ] T119 [US3] Write test: `retrieve()` — mock DB returns row with `customer_id="B"` for query from customer A → that row never in results (R5 memory leakage guard — query filter)
- [ ] T120 [US3] Write test: `retrieve()` — mock returns top-3 but one has cosine score 0.60 (below threshold 0.75) → only 2 returned (R7 context drift guard)
- [ ] T121 [US3] Write test: `retrieve()` — mock returns results with all scores below threshold → empty list returned (not an error)
- [ ] T122 [US3] Write test: `retrieve()` — no rows exist for this `customer_id` → returns empty list (cold start, no exception)
- [ ] T123 [US3] Write test: `retrieve()` — mock returns STALE row (`status="STALE"`) — STALE row NOT in results (FR-010b)
- [ ] T124 [US3] Implement `SemanticMemoryService.flag_stale(current_model_version: str, db: AsyncSession) -> int` — UPDATE `status=STALE` WHERE `model_version != current_model_version AND status = 'ACTIVE'`, return count of updated rows
- [ ] T125 [US3] Write test: `flag_stale()` — 3 rows with old version + 2 rows with current version → 3 flagged STALE, 2 remain ACTIVE
- [ ] T126 [US3] Write test: `flag_stale()` — all rows already current version → 0 rows changed, no exception

### 7b. Semantic Memory — Background Update

- [ ] T127 [US3] Implement `_update_semantic_memory(customer_id, session_id, summary_id, summary_text, db_factory)` async helper in `services/memory/background.py` — calls `SemanticMemoryService.store()`
- [ ] T128 [US3] Write test: `_update_semantic_memory()` — `store()` raises exception → error logged, not propagated
- [ ] T129 [US3] Write test: `_update_semantic_memory()` called inside `_maybe_summarize()` ONLY when summary INSERT succeeded (not on summarizer failure)

### 7c. CLI Re-embed Command

- [ ] T130 [US3] Add `reembed-semantic-memory` subcommand to `cli/rag_admin.py`: calls `flag_stale(current_version)` then re-embeds all STALE rows via `store()` loop, logs progress
- [ ] T131 [US3] Write test: `reembed-semantic-memory` dry-run flag → logs count of STALE rows, no rows updated

### 7d. Memory Retrieval Node

- [ ] T132 [US3] Create `core/agent/nodes/memory_retrieval.py` with `memory_retrieval_node(state: AgentState, db: AsyncSession) -> dict` — calls `SemanticMemoryService.retrieve()`, returns `memory_context` and `memory_retrieval_scores` state updates
- [ ] T133 [US3] Write test in `tests/unit/test_semantic_memory.py`: `memory_retrieval_node` with `customer_id=None` or missing → returns `{"memory_context": [], "memory_retrieval_scores": []}` with no exception (cold start / missing customer_id)
- [ ] T134 [US3] Write test: `memory_retrieval_node` with valid `customer_id` and mock service returning 2 results → `memory_context` has 2 entries, `memory_retrieval_scores` has 2 entries
- [ ] T135 [US3] Add `memory_retrieval_node` to `core/agent/graph.py` node registry and `REGISTERED_NODE_NAMES` list
- [ ] T136 [US3] Wire `memory_retrieval_node` edge in `build_graph()`: insert between `confidence_node` and `answer_node` on INFO/PRICING/COMPARISON/AVAILABILITY/COMPLAINT/NEGOTIATION/ORDER_PLACEMENT paths only; SMALLTALK and FOLLOW_UP go directly to answer_node (skip memory)
- [ ] T137 [US3] Write test in `tests/unit/test_router_node.py`: SMALLTALK intent routes to `answer_node` directly — `memory_retrieval_node` is NOT in the execution path
- [ ] T138 [US3] Implement `GET /memory/semantic/{customer_id}` in `api/routes/memory.py` — query `SemanticMemory` + join `ConversationSummary`, return counts for ACTIVE and STALE separately
- [ ] T139 [US3] Write test: `GET /memory/semantic/{customer_id}` response contains `active_count` and `stale_count` fields
- [ ] T140 [US3] Write integration test `test_semantic_recall_cross_session` in `tests/integration/test_memory_flow.py`: session A (customer X) → 22 messages with "máy lạnh 20 triệu" → summarize → store semantic memory → session B same customer X → memory_retrieval_node returns result with score ≥ 0.75

---

## Phase 8: Polish — Right to be Forgotten, Edge Cases & Final Integration

**Purpose**: Complete FR-019 (RTBF), protect against all edge cases from Known Risks R1–R11, final integration tests.

### 8a. Right to be Forgotten (FR-019)

- [ ] T141 Write contract test in `tests/contract/test_memory_api.py`: `DELETE /memory/customer/X` without `X-Admin-Key` → `401`
- [ ] T142 Write contract test: `DELETE /memory/customer/X` with valid key but pending HITL → `409` with `pending_pause_ids` field
- [ ] T143 Write contract test: `DELETE /memory/customer/X?confirm_delete_pending_hitl=true` → `200` with deletion counts
- [ ] T144 Implement `DELETE /memory/customer/{customer_id}` in `api/routes/memory.py`: check `HITLMetadata` for paused sessions → 409 if found without confirm param
- [ ] T145 Implement transactional cascade delete in `api/routes/memory.py`: within single `async with db.begin()` delete from `intent_tracking`, `sales_intent_log`, `semantic_memory`, `conversation_summaries`; if `confirm_delete_pending_hitl=true` also update `HITLMetadata.status = "abandoned"` for that customer first
- [ ] T146 Write test in `tests/unit/test_memory_api.py` (or route unit): deletion without pending HITL → all 4 tables deleted, response contains counts
- [ ] T147 Write test: deletion with 1 pending HITL and no confirm → 409 response body contains `pending_pause_ids`
- [ ] T148 Write test: deletion with pending HITL + `confirm=true` → HITL abandoned, all data deleted, `hitl_abandoned_count=1` in response
- [ ] T149 Write test: deletion — DB transaction fails mid-way (mock `session.commit` raising) → rollback, nothing deleted (atomicity test)
- [ ] T150 Write integration test `test_rtbf_complete` in `tests/integration/test_memory_flow.py`: create customer data in all 4 tables → DELETE → verify all 4 tables have 0 rows for that customer
- [ ] T150b Add `delete-customer` CLI subcommand to `cli/rag_admin.py`: takes `customer_id`, calls RTBF API endpoint or performs cascade delete directly; with `--dry-run` flag, logs deletion counts without modification (FR-019: parity with RAG admin interface per Article I)

### 8b. Embedding Governance Edge Cases

- [ ] T151 Write integration test `test_stale_embeddings_excluded_after_model_change` in `tests/integration/test_memory_flow.py`: insert semantic_memory rows with old `model_version` → change env `EMBED_MODEL` → call `retrieve()` → assert old rows NOT returned
- [ ] T152 Write test in `tests/unit/test_semantic_memory.py`: `retrieve()` with mismatched `model_version` in DB rows → returns empty list (not an error, just filtered out)
- [ ] T153 Write test: `store()` after `flag_stale()` → new row has current `model_version` and `status=ACTIVE`

### 8c. Connection Pool & Concurrency

- [ ] T154 Write integration test `test_connection_pool_under_load` in `tests/integration/test_memory_flow.py` (`@pytest.mark.integration`): simulate 15 concurrent `post_turn_tasks()` calls → assert no `TimeoutError` or pool exhaustion, all tasks complete
- [ ] T155 Write test: `IntentTracker.upsert_with_lock()` with pool connection at capacity (mock pool returning error) → `IntentLockConflictError` raised, not a pool exception (proper wrapping)

### 8d. Customer Identity Edge Cases

- [ ] T156 Write test: `retrieve()` with `customer_id` containing special characters (`tg:12345`) → query executes safely (no SQL injection via parameterized query)
- [ ] T157 Write test: `make_initial_state()` with `customer_id=""` (empty string) → raises `ValueError` (blank customer_id guard)
- [ ] T158 Write test: two sessions for same `customer_id` from different thread_ids → `IntentTracking` has ONE row (upsert, not insert), `session_id` updated to most recent thread

### 8e. Summarization Edge Cases

- [ ] T159 Write test in `tests/unit/test_summarizer.py`: `summarize()` with very short 2-message conversation → `open_questions` empty list, `products_discussed` empty list (no hallucination for sparse input)
- [ ] T160 Write test: `summarize()` when LiteLLM returns partial JSON (malformed) → Pydantic validation error caught, logged, fallback: empty `ConversationSummaryOutput` returned (no crash)
- [ ] T161 Write test: `_maybe_summarize()` when `should_summarize()` returns True but existing summary already exists for this session at exact `turn_count` → no duplicate inserted (idempotency guard)

### 8f. Graph Version & Checkpoint Safety

- [ ] T162 Write test in `tests/unit/test_hitl_service.py`: load checkpoint written by `GRAPH_SCHEMA_VERSION="004"` — mock state with a missing Week 5 field → `KeyError` caught → `_mark_incompatible` called, `INCOMPATIBLE` status returned
- [ ] T163 Write test: `make_initial_state()` with all required fields → produces valid state that can be serialized to JSON (no non-serializable types)

### 8g. Final Integration Test Suite

- [ ] T164 Write integration test `test_full_week5_happy_path` in `tests/integration/test_memory_flow.py`: (1) send 22 messages → summarize → embed → (2) restart → (3) new session same customer → memory recalled → (4) intent extracted → (5) admin queries intent list → lead appears with correct urgency
- [ ] T165 Write integration test `test_new_customer_cold_start` in `tests/integration/test_memory_flow.py`: brand-new `customer_id` → `memory_retrieval_node` returns empty → `answer_node` runs without memory block → no exception, normal response
- [ ] T166 Write integration test `test_post_turn_tasks_ttft_budget` in `tests/integration/test_memory_flow.py`: measure time from `graph.ainvoke()` return to response sent; assert delta ≤ 50ms even with `asyncio.sleep(0.2)` injected into a background task mock (SC-009 TTFT budget)

---

## Dependencies

```
Phase 1 (T001–T010) → Phase 2 (T011–T030)
Phase 2 → Phase 3 (US4: T031–T042)
Phase 2 → Phase 4 (US2: T043–T074)
Phase 2 → Phase 5 (US1: T075–T090)
Phase 5 → Phase 6 (US5: T091–T111)  [_maybe_summarize goes in background.py]
Phase 6 → Phase 7 (US3: T112–T140)  [semantic memory needs summary rows]
Phase 4 + Phase 7 → Phase 8 (Polish: T141–T166)
```

**User Story dependency order (can execute phases 3–5 in parallel after Phase 2)**:
```
Phase 3 (US4) ─────┐
Phase 4 (US2) ─────┤──→ Phase 8 (Polish)
Phase 5 (US1) ─────┤
Phase 6 (US5) ─────┤
Phase 7 (US3) ─────┘
```

Phase 3, 4, 5 have no dependency on each other — they CAN be executed in parallel after Phase 2 completes.

---

## Parallel Execution Examples

### Sprint A (after Phase 2): Execute Phase 3 + 4 + 5 in parallel
```
Developer 1: Phase 3 (US4) — T031–T042  [checkpoint durability]
Developer 2: Phase 4 (US2) — T043–T074  [intent extraction + admin API]
Developer 3: Phase 5 (US1) — T075–T090  [background dispatch + context injection]
```

### Sprint B (after Sprint A): Execute Phase 6 + 7 in parallel
```
Developer 1: Phase 6 (US5) — T091–T111  [summarization]
Developer 2: Phase 7 (US3) — T112–T140  [semantic memory]
→ Sync point: T129 (semantic memory chained inside summarizer)
```

### Within each phase — parallel opportunities (marked [P]):
- T004–T010: All test skeleton files (7 parallel)
- T014–T015: Two Pydantic models (parallel)
- T023–T027: ORM models (5 parallel — different classes)
- T043–T045: Contract tests (3 parallel — different endpoints)
- T070–T072: Route implementations (3 parallel — different handlers)

---

## Implementation Strategy

**MVP scope (deliver value first)**:
1. **Phase 1 + 2** — Foundation (non-negotiable, blocks everything)
2. **Phase 3 (US4)** — Checkpoint durability (highest risk, most impact for live system)
3. **Phase 4 (US2)** — Intent extraction (immediately usable by sales staff, independent of memory retrieval)
4. **Phase 5 (US1)** — Background dispatch (enables the memory pipeline)

**Deliver Week 6 integration-ready**: After MVP, `make_initial_state(customer_id=...)` is wired and all background tasks run. Week 6 simply passes `str(telegram_user.id)`.

**Deferrable (P2 stories)**:
- Phase 6 (US5): Summarization — system works without it; adds cost optimisation
- Phase 7 (US3): Semantic recall — requires Phase 6 data to be meaningful

---

## Summary

| Phase | User Story | Tasks | Edge Cases Covered |
|-------|-----------|-------|-------------------|
| Phase 1 | Setup | T001–T010 (10) | — |
| Phase 2 | Foundation | T011–T030 (20) | Empty customer_id guard (T019, T157) |
| Phase 3 | US4 Checkpoint Durability | T031–T042 (12) | Corrupt checkpoint (T035), size overflow (T037), cleanup skips HITL (T041) |
| Phase 4 | US2 Intent Tracking | T043–T074 (32) | No hallucination null fields (T054), race condition (T065), version conflict (T063–T064), stale version (T068) |
| Phase 5 | US1 Background Dispatch | T075–T090 (16) | Task failure isolation (T081–T082), TTFT not blocked (T084), cold start (T088) |
| Phase 6 | US5 Summarization | T091–T111 (21) | LLM failure graceful (T106), DB fail no dangling embed (T107), sparse input no hallucination (T159), malformed JSON (T160), idempotency (T161) |
| Phase 7 | US3 Semantic Memory | T112–T140 (29) | Memory leakage R5 (T119), context drift R7 (T120), STALE excluded (T123), cold start (T122), wrong dimension (T116), SMALLTALK bypass (T137) |
| Phase 8 | Polish & Edge Cases | T141–T166 (26) | RTBF atomicity (T149), embedding drift (T151–T153), pool load (T154), SQL injection (T156), full end-to-end (T164–T166) |
| **Total** | | **166 tasks** | **R1–R11 all covered** |
