# Data Model: Async Persistence & Memory

**Phase 1 Output** | Branch: `005-async-persistence-memory` | Date: 2026-03-11  
**Schema**: `agent_v1` (existing PostgreSQL schema — single-DB principle)  
**Depends on**: Week 4 tables (`checkpoints`, `hitl_metadata`, `review_actions`) already exist.

---

## 1. AgentState Extension (`core/agent/state.py`)

### 1a. IntentEnum — Add FOLLOW_UP, OTHER

```python
class IntentEnum(StrEnum):
    # Existing (Week 3)
    INFO_QUERY      = "INFO_QUERY"
    PRICING         = "PRICING"
    COMPARISON      = "COMPARISON"
    COMPLAINT       = "COMPLAINT"
    NEGOTIATION     = "NEGOTIATION"
    SMALLTALK       = "SMALLTALK"
    AVAILABILITY    = "AVAILABILITY"
    ORDER_PLACEMENT = "ORDER_PLACEMENT"
    # NEW (Week 5) — skip intent extraction for these
    FOLLOW_UP       = "FOLLOW_UP"    # "Ok", "Cảm ơn", "Được rồi"
    OTHER           = "OTHER"        # unclassifiable

# Intent extraction skip set (FR-011)
SKIP_INTENT_EXTRACTION: frozenset[IntentEnum] = frozenset({
    IntentEnum.FOLLOW_UP,
    IntentEnum.OTHER,
    IntentEnum.SMALLTALK,
})
```

### 1b. AgentState — New Week 5 Fields

```python
class AgentState(TypedDict):
    # --- existing fields (Week 1–4) unchanged ---
    session_id: str
    user_message: str
    messages: Annotated[list, add_messages]
    intent: str | None
    # ... all existing RAG, escalation, HITL fields ...

    # --- NEW: Week 5 Memory fields ---
    customer_id: str                     # Cross-session identity (Telegram user_id in Week 6)
    memory_context: list[dict]           # Top-K retrieved past summaries [{summary, score, thread_id}]
    memory_retrieval_scores: list[float] # Relevance scores for retrieved memories
    thread_summary_exists: bool          # True if a summary exists for this session_id
    sales_intent_skipped: bool           # True if turn intent was in SKIP_INTENT_EXTRACTION set
```

### 1c. `make_initial_state()` — Extended Signature

```python
def make_initial_state(
    user_message: str,
    session_id: str,
    customer_id: str,            # NEW: required — Week 6 passes telegram user_id
) -> AgentState:
    return {
        # ... existing defaults ...
        "customer_id": customer_id,
        "memory_context": [],
        "memory_retrieval_scores": [],
        "thread_summary_exists": False,
        "sales_intent_skipped": False,
    }
```

---

## 2. New Pydantic Output Models (`core/agent/state.py`)

### 2a. ConversationSummaryOutput

```python
class ConversationSummaryOutput(BaseModel):
    """
    Why this exists: Structured LLM output for conversation compression (FR-004).
    What it does: Enforces that the summarization LLM returns typed fields, no free text.
    """
    products_discussed: list[str] = Field(default_factory=list)
    customer_preference: str | None = None
    budget_stated: str | None = None        # e.g., "20–30M VND"
    open_questions: list[str] = Field(default_factory=list)
    summary_model: str = Field(description="LiteLLM model alias used")
    
    model_config = ConfigDict(strict=True)
```

### 2b. SalesIntentExtraction

```python
class UrgencyLevel(StrEnum):
    LOW     = "LOW"
    MEDIUM  = "MEDIUM"
    HIGH    = "HIGH"
    UNKNOWN = "UNKNOWN"

class SalesIntentExtraction(BaseModel):
    """
    Why this exists: Structured LLM output for sales intent (FR-011b, FR-013).
    What it does: Forces typed extraction; null/UNKNOWN for missing fields — no hallucination.
    """
    budget_range: str | None = None         # e.g., "under 15M VND" or null
    urgency_level: UrgencyLevel = UrgencyLevel.UNKNOWN
    product_interest: list[str] = Field(default_factory=list)
    decision_timeline: str | None = None    # e.g., "within this week"
    contact_preference: str | None = None   # e.g., "call me in the morning"

    model_config = ConfigDict(strict=True)
```

---

## 3. New ORM Tables (`models/schema.py`)

### 3a. ConversationSummary

```python
class ConversationSummary(Base):
    """
    Why this exists: Stores structured compressed summaries to reduce LLM token costs (FR-004, FR-005).
    What it does: One row per summary event per session. Linked to thread for context retrieval.
    """
    __tablename__ = "conversation_summaries"
    __table_args__ = (
        Index("idx_conv_summary_session_id", "session_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    products_discussed: Mapped[list] = mapped_column(JSONB, default=list)
    customer_preference: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_stated: Mapped[str | None] = mapped_column(String(100), nullable=True)
    open_questions: Mapped[list] = mapped_column(JSONB, default=list)
    summary_model: Mapped[str] = mapped_column(String(100), nullable=False)
    turn_count_at_summary: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
```

**Validation rules**:
- `session_id` must match an existing LangGraph `thread_id`
- `turn_count_at_summary` ≥ 20 (enforced at application layer)
- `summary_model` must be `LIGHT_CHAT_MODEL` (enforced in summarizer; validated in Tier 1 tests)

---

### 3b. SemanticMemory

```python
class EmbeddingStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    STALE  = "STALE"    # Set when embedding model changes (FR-010b)

class SemanticMemory(Base):
    """
    Why this exists: Enables cross-session memory retrieval via vector similarity (FR-007–009).
    What it does: Stores embedded conversation summaries keyed by customer_id for ANN search.
    """
    __tablename__ = "semantic_memory"
    __table_args__ = (
        Index(
            "idx_semantic_memory_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("idx_semantic_memory_customer_id", "customer_id"),
        Index("idx_semantic_memory_customer_status", "customer_id", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    customer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    summary_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.conversation_summaries.id", ondelete="CASCADE"),
        nullable=False,
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.EMBED_DIMENSION), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(
        String(150), nullable=False
    )  # f"{model_name}@{dimension}" e.g. "bge-m3@1024"
    status: Mapped[str] = mapped_column(
        String(20), default=EmbeddingStatus.ACTIVE, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
```

**Search query pattern** (FR-008 strict `customer_id` filter + relevance threshold):
```sql
SELECT sm.id, cs.products_discussed, cs.customer_preference, cs.budget_stated,
       1 - (sm.embedding <=> :query_embedding) AS score
FROM agent_v1.semantic_memory sm
JOIN agent_v1.conversation_summaries cs ON sm.summary_id = cs.id
WHERE sm.customer_id = :customer_id          -- MANDATORY: no cross-customer leakage
  AND sm.status = 'ACTIVE'
  AND sm.model_version = :current_model_version
ORDER BY sm.embedding <=> :query_embedding   -- cosine distance ascending
LIMIT :top_k;
-- Post-filter in Python: discard rows where score < MEMORY_RELEVANCE_THRESHOLD (0.75)
```

---

### 3c. SalesIntentLog

```python
class SalesIntentLog(Base):
    """
    Why this exists: Immutable audit trail of each intent extraction event (FR-011b, Article VII).
    What it does: One row per extraction run. Never updated — append-only for auditability.
    """
    __tablename__ = "sales_intent_log"
    __table_args__ = (
        Index("idx_sales_intent_log_customer", "customer_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    customer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    turn_number: Mapped[int] = mapped_column(nullable=False)
    triggering_intent: Mapped[str] = mapped_column(String(50), nullable=False)  # IntentEnum value
    budget_range: Mapped[str | None] = mapped_column(String(100), nullable=True)
    urgency_level: Mapped[str] = mapped_column(String(20), default="UNKNOWN", nullable=False)
    product_interest: Mapped[list] = mapped_column(JSONB, default=list)
    decision_timeline: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_preference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extraction_model: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
```

**Validation rules**:
- `urgency_level` must be one of `UrgencyLevel` enum values
- `triggering_intent` must NOT be in `SKIP_INTENT_EXTRACTION` (enforced at application layer)
- Append-only: no UPDATE operations ever issued on this table

---

### 3d. IntentTracking

```python
class IntentStatus(str, enum.Enum):
    NEW             = "NEW"
    ENGAGED         = "ENGAGED"
    AWAITING_QUOTE  = "AWAITING_QUOTE"
    CONTACTED       = "CONTACTED"
    CONVERTED       = "CONVERTED"
    LOST            = "LOST"

class IntentTracking(Base):
    """
    Why this exists: Per-customer mutable CRM view of current sales intent (FR-015, FR-015b).
    What it does: Upserted after each signal-bearing turn; protected by optimistic lock (version field).
    One row per customer_id.
    """
    __tablename__ = "intent_tracking"
    __table_args__ = (
        UniqueConstraint("customer_id", name="uq_intent_tracking_customer_id"),
        Index("idx_intent_tracking_urgency_status", "urgency_level", "intent_status"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    customer_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)   # most recent thread_id
    intent_status: Mapped[str] = mapped_column(
        String(30), default=IntentStatus.NEW, nullable=False
    )
    budget_range: Mapped[str | None] = mapped_column(String(100), nullable=True)
    urgency_level: Mapped[str] = mapped_column(String(20), default="UNKNOWN", nullable=False)
    product_interest: Mapped[list] = mapped_column(JSONB, default=list)
    decision_timeline: Mapped[str | None] = mapped_column(String(100), nullable=True)
    contact_preference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[int] = mapped_column(default=1, nullable=False)        # Optimistic lock (FR-015b)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    status_change_trigger: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "agent"|"admin"|"system"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
```

**State transitions** (FR-016 — every change must record trigger):
```
NEW → ENGAGED          (agent: first signal-bearing turn)
ENGAGED → AWAITING_QUOTE  (agent: user asked for a price quote)
AWAITING_QUOTE → CONTACTED  (admin: manually marks as contacted)
CONTACTED → CONVERTED  (admin: order placed)
CONTACTED → LOST       (admin: customer unresponsive)
ANY → ENGAGED          (agent: customer returns after CONVERTED/LOST — new opportunity)
```

**Optimistic lock update pattern** (FR-015b):
```python
stmt = (
    update(IntentTracking)
    .where(IntentTracking.customer_id == customer_id)
    .where(IntentTracking.version == expected_version)
    .values(**fields, version=expected_version + 1, last_updated=now())
    .returning(IntentTracking.version)
)
result = await db.execute(stmt)
if result.rowcount == 0:
    # Version conflict → re-read, merge, retry (max 3x, backoff 50ms/100ms/200ms)
```

---

## 4. New Config Settings (`core/config.py`)

```python
class Settings(BaseSettings):
    # ... existing settings ...

    # Week 5: Memory layer
    MEMORY_SUMMARY_THRESHOLD: int = Field(default=20, ge=5, le=100)
    # Summarize when message count hits this threshold; re-summarize every 10 msgs after first summary

    MEMORY_RELEVANCE_THRESHOLD: float = Field(default=0.75, ge=0.0, le=1.0)
    # Minimum cosine similarity for semantic memory to be included in context (FR-008)

    MEMORY_TOP_K: int = Field(default=3, ge=1, le=10)
    # Maximum past summaries to retrieve per session start

    CHECKPOINT_SIZE_WARN_BYTES: int = Field(default=1_048_576)  # 1MB
    # FR-001b: Log warning when checkpoint payload exceeds this size

    CHECKPOINT_RETENTION_DAYS: int = Field(default=90, ge=1)
    # FR-001c: Checkpoints older than this for resolved conversations are cleanup-eligible
```

---

## 5. New Graph Node (`core/agent/nodes/memory_retrieval.py`)

```python
async def memory_retrieval_node(state: AgentState, db: AsyncSession) -> dict:
    """
    Why this exists: Surfaces relevant past conversations before answer generation (FR-008).
    What it does: Semantic search over customer's past summaries; populates memory_context.
    Skipped if customer_id is missing or no past memories exist (cold start — FR edge case).
    """
    customer_id = state.get("customer_id")
    if not customer_id:
        return {"memory_context": [], "memory_retrieval_scores": []}

    results = await semantic_memory_service.retrieve(
        db=db,
        customer_id=customer_id,         # FR-008: strict customer_id filter
        query=state["user_message"],
        top_k=settings.MEMORY_TOP_K,
        min_score=settings.MEMORY_RELEVANCE_THRESHOLD,  # FR-008 score floor
    )
    return {
        "memory_context": [r.to_context_dict() for r in results],
        "memory_retrieval_scores": [r.score for r in results],
    }
```

**Graph placement**: `memory_retrieval_node` is inserted between `confidence_node` and `answer_node` in `build_graph()`. It does NOT run for SMALLTALK/FOLLOW_UP (router short-circuits to answer_node directly).

---

## 6. DB Schema Diagram

```
agent_v1
│
├── checkpoints (LangGraph, Week 4)         ─── thread_id ──────────────────┐
├── hitl_metadata (Week 4)                  ─── session_id ─────────────────┤
├── review_actions (Week 4)                 ─── session_id ─────────────────┤
│                                                                            │
├── conversation_summaries  ─── session_id (= thread_id) ──────────────────┘
│       id, session_id, customer_id                       ─── customer_id ──┐
│       products_discussed, customer_preference                              │
│       budget_stated, open_questions, summary_model                        │
│       turn_count_at_summary, created_at                                   │
│              │ (FK: summary_id)                                           │
├── semantic_memory                                        ─── customer_id ─┤
│       id, customer_id, session_id, summary_id                             │
│       embedding vector(1024), embedding_model                             │
│       model_version "bge-m3@1024", status ACTIVE/STALE                   │
│       [HNSW index: cosine, m=16, ef_construction=64]                      │
│                                                                            │
├── sales_intent_log (append-only)           ─── customer_id ──────────────┤
│       id, customer_id, session_id, turn_number                            │
│       triggering_intent, budget_range, urgency_level                      │
│       product_interest[], decision_timeline, created_at                   │
│                                                                            │
└── intent_tracking (1 row per customer)     ─── customer_id ──────────────┘
        id, customer_id, session_id (latest)
        intent_status, version (optimistic lock)
        budget_range, urgency_level, product_interest[]
        decision_timeline, contact_preference
        last_updated, status_changed_at, status_change_trigger
```

---

## 7. Post-Turn Background Dispatch Flow

```
graph.ainvoke(state, config)
       │
       ▼
Customer response RETURNED immediately
       │
       ▼ asyncio.create_task (fire & forget)
┌──────────────────────────────────────────────┐
│  asyncio.gather(return_exceptions=True)      │
│  ┌────────────────────────────────────────┐  │
│  │ Task 1: _check_checkpoint_size()       │  │
│  │   Read checkpoint bytes from DB        │  │
│  │   Log WARN if > CHECKPOINT_SIZE_WARN   │  │
│  └────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────┐  │
│  │ Task 2: _maybe_extract_intent()        │  │
│  │   Check primary_intent ∈ SKIP_SET?     │  │
│  │   → skip: set sales_intent_skipped     │  │
│  │   → run: LiteLLM (LIGHT_CHAT_MODEL)    │  │
│  │          SalesIntentExtraction output  │  │
│  │          Append to sales_intent_log    │  │
│  │          Upsert intent_tracking        │  │
│  │          (optimistic lock, max 3 retry)│  │
│  └────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────┐  │
│  │ Task 3: _maybe_summarize()             │  │
│  │   Count messages in state              │  │
│  │   >= MEMORY_SUMMARY_THRESHOLD?         │  │
│  │   → LiteLLM (LIGHT_CHAT_MODEL)         │  │
│  │     ConversationSummaryOutput          │  │
│  │     Insert conversation_summaries      │  │
│  └────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────┐  │
│  │ Task 4: _update_semantic_memory()      │  │
│  │   Depends on Task 3 completing first   │  │
│  │   (chained within Task 3, not gather)  │  │
│  │   Embed summary → Insert semantic_mem  │  │
│  └────────────────────────────────────────┘  │
│  Any task Exception → logger.error() only   │
│  Never propagated to next customer turn     │
└──────────────────────────────────────────────┘
```

> Note: Task 4 (semantic memory update) is chained inside Task 3 because it depends on the new summary being committed. It is NOT in the gather top level.
