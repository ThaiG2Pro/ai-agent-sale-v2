# Research: Async Persistence & Memory

**Phase 0 Output** | Branch: `005-async-persistence-memory` | Date: 2026-03-11  
**Status**: All NEEDS CLARIFICATION resolved ✅

---

## Decision 1: AsyncPostgresSaver — Already Wired, No Reinstall

**Decision**: AsyncPostgresSaver from `langgraph-checkpoint-postgres` is already initialised in Week 4 (`core/agent/graph.py`, FastAPI lifespan). Week 5 requires zero changes to the checkpointer setup. The `checkpoints`, `checkpoint_writes`, `checkpoint_blobs`, and `checkpoint_migrations` tables already exist in `agent_v1`.

**What Week 5 adds on top**: Four new tables (`conversation_summaries`, `semantic_memory`, `sales_intent_log`, `intent_tracking`) live alongside the checkpoint tables. They are linked via `session_id` (= LangGraph `thread_id`) and `customer_id` (cross-session).

**Rationale**: Reusing existing infrastructure avoids double-setup bugs and stays lean (Article II). The AsyncPostgresSaver already handles FR-001 (checkpoint durability across restarts).

**Alternatives considered**:
- LangGraph's `MemorySaver` (in-memory): rejected — violates Article VII (stateless runtime).
- Custom checkpointer from scratch: rejected — AsyncPostgresSaver is the LangGraph-official pattern, already proven in Week 4.

---

## Decision 2: Parallel Post-Turn Background Tasks via `asyncio.create_task`

**Decision**: After `graph.ainvoke()` returns and the response is sent to the user, dispatch the four post-turn operations as parallel, fire-and-forget tasks using `asyncio.create_task` with `asyncio.gather(*tasks, return_exceptions=True)`.

```python
# In the API layer (after ainvoke):
async def _post_turn_background(session_id: str, customer_id: str, state: AgentState, db_factory):
    """Fire-and-forget: dispatched AFTER customer response is returned."""
    tasks = [
        _save_intent_if_signal_bearing(customer_id, state, db_factory),
        _maybe_summarize(session_id, state, db_factory),
        _update_semantic_memory(customer_id, session_id, db_factory),
        _check_checkpoint_size(session_id, db_factory),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.error("post_turn_task[%d] failed: %s", i, r)
            # FR-003b: failure is logged, NOT propagated to the next customer turn

asyncio.create_task(_post_turn_background(session_id, customer_id, state, db_factory))
# ↑ create_task returns immediately; customer response is already sent
```

**Rationale**: `asyncio.create_task` schedules work on the running event loop without blocking the caller (Article V). FastAPI `BackgroundTasks` runs after the HTTP response but is tied to the request lifecycle — `create_task` is fully decoupled. `return_exceptions=True` ensures one failing task never kills the others (FR-003b).

**Alternatives considered**:
- Celery: rejected — prohibited by constitution (no Celery, no Redis).
- FastAPI BackgroundTasks: acceptable but runs after HTTP response, not truly parallel with other tasks.
- asyncio.TaskGroup (Python 3.11+): valid but raises on first exception; `gather(return_exceptions=True)` gives more control here.

---

## Decision 3: Optimistic Locking for IntentTracking

**Decision**: Use SQLAlchemy Core `UPDATE` with `WHERE version = :expected` and `RETURNING version`. Check `rowcount == 1`. On conflict (rowcount == 0), re-read the row, merge fields (recency wins for current session's values), and retry up to 3 times with exponential backoff (0.05s, 0.1s, 0.2s).

```python
stmt = (
    update(IntentTracking)
    .where(IntentTracking.customer_id == customer_id)
    .where(IntentTracking.version == expected_version)
    .values(
        **new_fields,
        version=expected_version + 1,
        last_updated=datetime.now(UTC),
    )
    .returning(IntentTracking.version)
)
result = await db.execute(stmt)
if result.rowcount == 0:
    # Conflict: read-merge-retry
```

**Rationale**: Prevents stale data overwrite when two rapid messages trigger concurrent intent extractions (R6 in Known Risks). No pessimistic locking (SELECT FOR UPDATE) avoids connection hold time, which is critical on a 20-connection pool. Pure SQL — no ORM session magic (Article II.2).

**Merge strategy on conflict**: current session's extracted fields override stored fields (recency wins per FR-014). If the conflicting write was from a newer turn in the same session, last-writer-wins is safe because both extractions read from the same conversation history.

---

## Decision 4: HNSW Index Parameters for `semantic_memory`

**Decision**: Use the same HNSW parameters already proven in `text_embeddings` and `semantic_cache`:
- `m=16` — 16 bi-directional links per node (good recall/memory balance at SME scale)
- `ef_construction=64` — build-time candidate set (sufficient for <50k entries)
- Operator: `vector_cosine_ops` (cosine similarity, matches L2 semantic cache pattern)
- Runtime `ef_search=40` — set per-session via `SET LOCAL hnsw.ef_search = 40` for recall tuning without global impact

```sql
CREATE INDEX idx_semantic_memory_embedding
ON agent_v1.semantic_memory USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

**Minimum relevance threshold**: 0.75 (configurable via `MEMORY_RELEVANCE_THRESHOLD` env var). Entries below threshold are discarded even if ranked top-K.

**Rationale**: Matching existing index parameters ensures consistent performance characteristics across all vector tables (Article XI ADR consistency). 500ms p95 target is achievable with HNSW on 500+ entries on modest local hardware — confirmed by existing semantic cache benchmarks.

**Document in**: `docs/adr/ADR-005-memory-hnsw-embedding-governance.md`

---

## Decision 5: IntentEnum Extension — FOLLOW_UP and OTHER

**Decision**: Extend the existing `IntentEnum` in `core/agent/state.py` with two new values:
```python
class IntentEnum(StrEnum):
    # ... existing values ...
    FOLLOW_UP = "FOLLOW_UP"     # "Ok", "Cảm ơn", "Hiểu rồi" — skip intent extraction
    OTHER = "OTHER"             # Unclassifiable — skip intent extraction
```

Intent extraction is skipped if `primary_intent in {IntentEnum.FOLLOW_UP, IntentEnum.OTHER, IntentEnum.SMALLTALK}`. SMALLTALK is also added to the skip set because "Xin chào" carries no sales signal.

**Signal-bearing intents** (extraction runs):
`INFO_QUERY, PRICING, COMPARISON, COMPLAINT, NEGOTIATION, AVAILABILITY, ORDER_PLACEMENT`

**Rationale**: Extending the existing enum preserves backward compatibility (existing router node tests still pass). Adding SMALLTALK to the skip set is a zero-cost refinement aligned with R4 (token waste risk).

---

## Decision 6: Embedding Model Versioning (`model_version` composite key)

**Decision**: Store `model_version` as a composite string `f"{model_name}@{dimension}"` (e.g., `"bge-m3@1024"`). This is derived from `settings.EMBED_MODEL` and `settings.EMBED_DIMENSION` — no new config required.

On semantic memory search, filter: `WHERE model_version = :current_version AND status != 'STALE'`.

When model changes (EMBED_MODEL or EMBED_DIMENSION changes):
1. Operator runs `python -m cli.rag_admin reembed --table semantic_memory` (CLI flag on existing RAG admin CLI per Article I)
2. CLI sets `status = 'STALE'` for all old-version rows, re-computes embeddings, sets `status = 'ACTIVE'`
3. Until re-embedding completes, stale rows are excluded from search — "no memory" is better than wrong memory (Article IX)

**Rationale**: Composite version key is self-contained — no separate version table. The RAG admin CLI already exists (Week 1, Task 1.12) and is the right home for this operation.

---

## Decision 7: Graph Schema Versioning for Checkpoint Compatibility

**Decision**: Add a `GRAPH_SCHEMA_VERSION = "005"` string constant in `core/agent/graph.py`. When `AsyncPostgresSaver` loads a checkpoint, wrap deserialization in a try/except. On `KeyError`, `ValidationError`, or `TypeError` (signs of schema mismatch), catch and call `_mark_checkpoint_incompatible(session_id, checkpoint_id)` which:
1. Updates `HITLMetadata.status = 'abandoned'` if a pending pause exists
2. Logs `checkpoint_id`, detected error, current `GRAPH_SCHEMA_VERSION`
3. Returns an `INCOMPATIBLE` error to the caller (FR-018)

**No automatic migration**: graph state migrations are too risky to automate. Operator must manually inspect and decide: discard or hand-migrate.

**Rationale**: Silent checkpoint failures cause ghost sessions — customers in stuck state with no admin visibility. Explicit error surfacing on `/review` (FR-018) gives the operator a recoverable path.

---

## Decision 8: Right to be Forgotten — Transactional Cascade Delete

**Decision**: Single async transactional delete across all four new tables + `HITLMetadata`/`ReviewAction` for the given `customer_id`. Before executing:
1. Check `HITLMetadata` for any `status = 'paused'` rows for this customer
2. If found: return `HTTP 409` with list of pending pause IDs and require explicit `?confirm_delete_pending_hitl=true` query param
3. If clean (or confirmed): execute in one transaction, return count of deleted rows per table (audit log)

**Rationale**: Transactional delete prevents partial data erasure. The HITL confirmation gate (FR-019) prevents accidental deletion of orders-in-flight. Single transaction means either all data is deleted or nothing is (atomicity).

---

## Decision 9: Conversation Summary — Model and Trigger

**Decision**:
- **Model**: `settings.LIGHT_CHAT_MODEL` (economy tier — summarization is simple, not a reasoning task). Assert `model_used == LIGHT_CHAT_MODEL` in Tier 1 eval tests (Article XII).
- **Trigger**: After every turn, check `len(state["messages"]) >= settings.MEMORY_SUMMARY_THRESHOLD` (default: 20). If triggered and no summary exists for this `session_id`, fire summarization as a background task. If a summary already exists for this session (progressive re-summarization), re-summarize when `messages_since_last_summary >= 10`.
- **Output**: Pydantic model `ConversationSummaryOutput` with fields: `products_discussed: list[str]`, `customer_preference: str | None`, `budget_stated: str | None`, `open_questions: list[str]`, `summary_model: str`.
- **Failure**: If the LLM call fails, log and skip. Next turn will retry. Customer experience unaffected (FR-006).

---

## Decision 10: Week 6 Integration Contract

**Decision**: `customer_id` is a string that Week 6 (Telegram webhook) will populate from `message.from_user.id` (Telegram integer cast to string: `str(update.effective_user.id)`). Week 5 uses `customer_id` throughout without knowing the source. `make_initial_state()` factory will add a `customer_id` parameter (required). Week 6 passes it in.

`thread_id` (= `session_id`) in Week 6 will be `f"telegram:{chat_id}"` — namespaced to prevent collision with Web sessions. Week 5 is agnostic to the prefix.

**Week 7 integration surface**: The `model_trace` table (Week 1) receives `memory_retrieved: bool` and `intent_extraction_skipped: bool` from `AgentState` — no changes to model_trace schema needed. The semantic cache hit rate metric (Week 7) is separate from semantic memory — same table name prefix avoidance is documented in ADR-005.
