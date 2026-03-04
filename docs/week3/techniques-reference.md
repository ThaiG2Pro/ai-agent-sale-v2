# 🧠 Week 3 Technical Reference — Keywords, Methods, Decisions (No Code)

> **Purpose:** Quick reference for technical concepts, methods, decisions, and notes.  
> **Use:** Architecture decisions, planning, knowledge base.  
> **No Code:** Implementation details deferred to techniques-overview.md or reports 3.1-3.9.md

---

## 1. State Management — Task 3.1, 3.4

### State Schema Choices
- **TypedDict** — Internal state, serializable, zero overhead
- **Pydantic v2** — System boundaries, validation, API/LLM I/O
- **Dataclass** — Simple state, default values

### Reducers & Accumulation
- **Default** — Replace/overwrite
- **operator.add** — Accumulate lists (messages, results)
- **add_messages** — Deduplication by ID (critical for retries)
- **MAX reducer** — Keep maximum value (risk_score never decreases)
- **Custom reducer** — Domain-specific logic

### Checkpointing Backends
- **MemorySaver** — Dev local, loses on restart
- **SqliteSaver** — Dev/test, single file
- **AsyncPostgresSaver** — Production, JSONB, connection pool

### Checkpoint TTL Policy
- **Durability='exit'** — Reduce checkpoint frequency
- **Point-in-time snapshots** — Thread-level recovery
- **Cleanup strategy** — Keep N recent per thread_id

### State Evolution & Schema Safety
- ✅ **Safe:** Add field with default, remove unused field
- ❌ **Unsafe:** Rename field, change type, add required without default
- **Pattern:** state.get("field", default_value) defensive access
- **Validation:** @field_validator for type checking
- **Version migration:** V1 → V2 with fallback defaults

### State Serialization Issues
- **Non-JSON objects** — Inherit Serializable class
- **Enum type loss** — Store __qualname__ for nested enums
- **UUID in Postgres** — Store as UUID object, not string
- **Message deduplication** — ID-based, prevents duplicates on retry

### State Bloat Prevention
- **Reference pattern** — S3/storage keys, not full objects
- **Message pruning** — Token count or message count limits
- **Summarization node** — Compress history
- **RemoveMessage** — Permanent deletion

---

## 2. Async Tools & Non-blocking I/O — Task 3.2

### Tool Design Principles
- **async def required** — All tools must be async
- **No blocking calls** — requests→httpx, sleep→asyncio.sleep
- **Clean signatures** — Only LLM-generated parameters
- **Dependency injection** — System resources via closures/factories
- **Docstrings required** — LLM needs clear descriptions

### Resilience Patterns
- **Circuit Breaker** — Fail fast on repeated failures, exponential backoff
- **Connection Pool** — Persistent across FastAPI lifespan
- **Timeout + Retry** — Exponential backoff (1s, 2s, 4s, 8s...)
- **Graceful degradation** — Fallback to safe response
- **Fallback chains** — Primary → Secondary → Local model

### Connection Management
- **Pool size formula** — (servers × users × queries/req × latency) + buffer
- **HTTP/2 multiplexing** — Reduce connection overhead
- **Keepalive expiry** — Close idle after 5s
- **Limits.max_connections** — Global pool limit

### Event Loop Safety
- **Blocking detection threshold** — 50ms (production warning)
- **sys.setprofile** — Monitor every function call/return
- **anyio.to_thread.run_sync** — CPU-bound work off event loop
- **Loop context preservation** — ContextVar for async context
- **No blocking in FastAPI** — Serialize HTTP client per event loop

---

## 3. Pydantic Tool Schema — Task 3.3

### Schema Validation Modes
- **Strict mode** — No type coercion (LLM output)
- **Lax mode** — Permissive parsing (external API)
- **Field constraints** — pattern, ge, le, min_length, max_length
- **Annotated validators** — Embed logic in type definition

### Tool Input Protection
- **Schema constraints** — Reject invalid format at definition
- **Sanitization** — shlex.quote for shell, bleach for HTML
- **Context isolation** — User input never overwrites system commands
- **Intent analysis** — Detect anomalous patterns

### Tool Output Validation
- **Response parsing** — Pydantic strict mode
- **Error handling** — Custom validators with fallback
- **Type coercion** — Be permissive for external APIs
- **Nested validation** — Parent validates child models

### Injection Attack Prevention
- **Tool argument injection** — Schema constraints + sanitization
- **Schema drift detection** — Structural diff vs baseline
- **Mock rot prevention** — Capture real responses periodically
- **Zero-config baseline** — Don't compare values, only shape/type

---

## 4. Graph Compile + Mermaid Visualization — Task 3.4

### Compilation & Pregel Model
- **.compile()** — StateGraph → Pregel executable
- **Orphaned nodes detection** — Unreachable nodes in graph
- **Channel consistency** — All state keys defined
- **Supersteps** — Plan → Execute → Update (3 stages)
- **Deterministic updates** — No race conditions

### PostgreSQL 17 Features for Checkpointing
- **Incremental VACUUM** — 45min → 4min (track dirty pages)
- **Bi-directional Index Scans** — 35% cost reduction (no separate index)
- **JSON_TABLE native** — Direct SQL queries on JSONB state
- **Parallel COPY** — 4× faster state recovery

### Checkpointer vs Store
- **Checkpointer** — Short-term (within thread), HITL recovery
- **Store** — Long-term (cross-thread), learning & patterns
- **Single-DB architecture** — PostgreSQL for both
- **JSONB storage** — Full state snapshot

### Mermaid Logic Validation
- **Dead-end nodes** — No path to END (logic error)
- **Infinite loops** — No exit mechanism detected
- **Cyclomatic complexity > 15** — Too complex, refactor
- **> 50 nodes** — Split into subgraphs
- **Visualization automation** — Auto-export on code change

### Graph Cleanup Strategy
- **durability='exit'** — Reduce checkpoint writes
- **Thread-level retention** — Keep N most recent per thread
- **Operational logs** — Treat checkpoints as logs, not archive

---

## 5. Router Node — Intent Classification & Escalation — Task 3.5, 3.7

### Intent Classification Approach
- **Intent-first routing** — Classify before retrieve
- **Intent-first vs RAG-first** — More efficient, lower cost, safer
- **Confidence scoring** — Logprobs from model
- **Long-tail intent cache** — Vector match for low-frequency intents
- **Speculative routing** — Start escalation check while classifying

### Intent Categories (Business Rules)
- **COMPLAINT** → Escalate immediately (sentiment-driven)
- **NEGOTIATION** → Sales authority needed
- **REFUND** → Finance approval required
- **INFO_QUERY** — Standard retrieval
- **GENERAL** — Default fallback

### Command API (LangGraph 2026)
- **Runtime routing** — Command(goto="...", update={...})
- **Dynamic decisions** — Based on state, not static edges
- **Flexible escalation** — Conditions evaluated per message

### Model Escalation Tiers
- **Tier 1: Local** — Ollama/Qwen 3.2 (0 VND)
- **Tier 2: Mid-tier** — GPT-4o-mini / Gemini Flash (low cost)
- **Tier 3: Premium** — GPT-4o / Claude 3.5 (high accuracy)
- **Tier 4: Reasoning** — o1/o3 / DeepSeek R1 (complex logic)
- **Tier 5: Human** — Manual escalation

### Escalation Signals (30+ Categories)

#### Intent-Based (P0 Critical)
- COMPLAINT — Immediate escalation
- NEGOTIATION — Requires pricing authority
- REFUND — Finance approval
- LEGAL keywords — Legal dept

#### Sentiment-Based (P1 High)
- Anger > 0.8 — Use empathy model
- Frustration pattern — 3+ keywords
- Urgency keywords — "ASAP", "immediately"

#### Complexity-Based (P1 High)
- Multi-step logic — > 3 tools needed
- Long context — > 2000 tokens
- Ambiguous intent — confidence < 0.6

#### Risk-Based (P0-P1)
- VIP customer — Direct to specialist
- High-value order — > $1000
- Policy violation — Security issue
- Repeated failures — 3× tool errors

#### Operational (P1-P2)
- Tool error count >= 3 — System issue
- Timeout pattern — Network degradation
- Budget exceeded — Cost control

#### Security (P0 Critical)
- Prompt injection detected — Quarantine
- RAG injection attempt — Review node
- Anomaly pattern — Unusual access

#### Legal/Compliance (P0 Critical)
- "Lawsuit", "legal" → Legal dept
- GDPR request → Compliance officer
- PCI/HIPAA mention → Compliance

### Escalation Signal Priority
- **P0 Critical** — Override all logic (intent, security, legal)
- **P1 High** — Multi-factor evaluation
- **P2 Medium** — Pattern recognition

### LiteLLM Router Configuration
- **Model list** — Named tiers with fallback chains
- **Cross-provider fallback** — Primary → Secondary → Local
- **Num_retries** — Default 3 with exponential backoff
- **Response cost tracking** — Real-time budget monitoring

### Bảo vệ — Denial of Wallet (EDoS)
- **Instruction boundary** — System > User > Data
- **XML tag salting** — Randomized boundaries
- **Budget caps** — Per virtual key / team
- **Validator agents** — Response firewall
- **Anomaly detection** — token_velocity, premium_ratio

### Least Privilege Node Identities
- **Per-node API keys** — Limited scope per function
- **Rate limits** — Per-node throttling
- **Resource quotas** — Memory, token limits

---

## 6. Step-level Streaming — Task 3.6

### Streaming Modes
- **values** — Full state snapshot per step
- **updates** — Delta only (changed fields)
- **messages** — LLM tokens + metadata
- **custom** — Domain-specific events
- **debug** — Full execution trace

### Streaming Combination
- **updates + messages** — State changes + typing effect

### Server-Sent Events (SSE)
- **HTTP standard** — No WebSocket needed
- **Auto-reconnect** — Client-side retry
- **data: {json}\n\n** — Required format
- **X-Accel-Buffering: no** — Disable proxy buffering
- **Cache-Control: no-cache** — Prevent caching

### Cancel-on-Disconnect Safety
- **anyio.create_task_group** — Monitor disconnect
- **http.disconnect event** — Catch client close
- **asyncio.shield()** — Protect DB writes from cancellation
- **Graceful cleanup** — Complete transactions before abort

### Observability in Streaming
- **thread_id** — Conversation history
- **run_id** — Single execution
- **correlation_id** — Link FastAPI ↔ LangGraph logs
- **ContextVar** — Preserve across async
- **Stream filtering** — Block PII, internal state

### AG-UI Protocol (2026)
- **Standard events** — TEXT_MESSAGE_CONTENT, TOOL_CALL_START, STATE_DELTA
- **Backend/Frontend decoupling** — Agent changes don't break UI

---

## 7. Confidence Scoring Integration — Task 3.8

### Two Core Scores
- **Similarity score** — Bi-encoder/pgvector (fast, broad)
- **Rerank score** — Cross-encoder (slow, precise)

### Similarity (Bi-Encoder)
- **Formula** — Cosine distance A·B / ||A|| ||B||
- **Fast** — Suitable for million+ documents
- **Weakness** — Ignores negation, logic, constraints

### Rerank (Cross-Encoder)
- **Attention** — Query + document same model
- **Sigmoid normalization** — Logits → [0, 1]
- **Detects** — Negation, conditions, importance
- **Slow** — Evaluate after filtering

### Confidence Fusion
- **Formula** — (1-α)·similarity + α·rerank
- **α=0.3** — Recall-focused (discovery)
- **α=0.5** — Balanced (general)
- **α=0.7** — Precision-focused (default, technical/legal)

### Min-Max Scaling
- **Normalization** — (x - min) / (max - min)
- **Before fusion** — Equalize score ranges

### Semantic vs Contextual Relevance
- **Semantic search** — Vector proximity only
- **Contextual relevance** — Logic & intent match
- **Document information gain (DIG)** — Jargon/synonyms add value

### Confidence Threshold Policy
- **> 0.85** — Automatic (high trust)
- **0.65–0.75** — SME sweet spot (balanced)
- **0.65–0.75** — Adjust by domain impact
- **< 0.5** — Hallucination risk high

### Threshold Tuning Strategy
1. **Baseline analysis** — Collect 500+ interactions, analyze distribution
2. **A/B testing** — Test candidates (0.60, 0.65, 0.70) vs current
3. **Cost formula** — λ₁·escalation_cost + λ₂·hallucination_cost + λ₃·latency
4. **Auto-adjust** — escalation_rate > 40% → lower; hallucination > 5% → raise

### 4 Chỉ số Vàng (Golden Metrics)
- **similarity_score** — Raw vector match
- **rerank_score** — Contextual relevance
- **model_used** — Which tier (for evaluation)
- **escalation_flag** — Whether escalated

### Confidence Guardrail Logic
- **confidence >= 0.7** → Generator node
- **confidence < 0.7** → Safe response + escalation

### Security: Prevent Score Manipulation
- **Compute backend-only** — Python/NumPy, not LLM
- **Separate from prompt** — Score is code logic
- **Immutable metadata** — Audit trail preservation

### Edge Cases
- **Low similarity + low rerank** → Compound escalation
- **High similarity, low rerank** → Reranker catches opposite intent
- **Both low** → KB gap suspected

---

## 8. Contract Tests for Tools — Task 3.9

### Contract Testing vs Unit Testing
- **Unit** — Internal logic
- **Contract** — Tool I/O interface (ERP, Inventory, Order)
- **Coverage** — Exact schema match

### TDD 2026 Pattern
- **Interface-first** — Pydantic schema before logic

### Test Stack
- **pytest-asyncio** — Standard for async tests
- **respx** — HTTP mock at transport layer

### Required Test Scenarios (5)
1. **200 OK** — Valid Pydantic schema
2. **404 Not Found** — Tool handles error gracefully
3. **429 Too Many** — Backoff + retry validation
4. **500 Server Error** — Graceful degradation
5. **ReadTimeout** — side_effect=httpx.ConnectTimeout

### Pydantic V2 Modes
- **Strict mode** — No coercion (LLM input)
- **Lax mode** — Permissive (external API)
- **Annotated validators** — Cleanup logic in type

### Contract Definition
- **Field(pattern=r"...")** — Reject invalid format
- **Field(ge=, le=)** — Range constraints
- **Custom validators** — Complex rules

### Tool Argument Injection Prevention
- **Schema constraints** — Pattern matching, enums
- **Sanitization** — bleach for HTML, shlex for shell
- **Context isolation** — Data ≠ command
- **Intent analysis** — Detect anomalies

### Secret Leakage Prevention
- **SecretStr** — Pydantic type, masks in logs
- **SensitiveLogFilter** — Regex redaction

### Schema Drift Detection
- **Mock rot** — Old mocks give false confidence
- **Structural diff** — Shape only, not values
- **Baseline capture** — Real API responses periodically
- **Breaking changes** — Type/field removal = error

### Definition of Done (3.9)
- ✅ pytest 100% pass tests/tools/
- ✅ All edge cases handled
- ✅ No secrets in logs post-test

---

## 9. Advanced Production Techniques

### 9.1 Human-in-the-Loop (HITL)
- **Mandatory nodes** — Checkout, pricing, confirmation, refunds
- **interrupt_before** — Pause graph at critical nodes
- **State checkpoint** — Save full state for resume
- **Admin approval** — Resume after decision

### 9.2 Observability Stack
- **Dev** — Arize Phoenix (self-hosted, offline)
- **Staging/Prod** — Logfire (cloud) or LangSmith
- **Fallback** — Python standard logging (always works)
- **Toggle** — ENV-based backend selection

### 9.3 Event Loop Management
- **RuntimeError: Task attached to different loop** — HTTP client context issue
- **Solution** — FastAPI lifespan + app.state sharing
- **Context preservation** — ContextVar across async

### 9.4 Event Loop Blocking Detection
- **Threshold** — 50ms warning (production)
- **Method** — sys.setprofile profiling
- **Detection** — Frame entry/return duration
- **Use case** — Debug latency in production

### 9.5 Operational KPIs (8 Metrics)

#### Core Metrics (Weekly)
1. **Containment rate** — % handled by AI (target > 70%)
2. **Routing accuracy** — Intent classification (target > 95%)
3. **Hallucination rate** — Inaccurate responses (target < 5%)
4. **Cost per request** — Financial tracking
5. **Model distribution** — Tier usage breakdown

#### Advanced Metrics (Monthly)
6. **Cache hit ratio** — Semantic cache effectiveness (target 40%)
7. **Escalation distribution** — KB gap identification
8. **Response latency P95** — UX metric (target < 3s)

### 9.6 Edge Cases & Security

#### Semantic Similarity Gap
- **Scenario** — High similarity (0.92), opposite intent
- **Example** — "Cancel subscription" vs "Manage subscription"
- **Signal** — Reranker detects negation → confidence drops
- **Prevention** — Cross-encoder catches intent mismatch

#### Cancel-on-Disconnect Race Condition
- **Risk** — Client closes, agent still writing corrupt state
- **Prevention** — asyncio.shield() protects DB commit
- **Pattern** — Check http.disconnect, cancel gracefully

#### HTML Injection in Address Fields
- **Attack** — `<script>` in address parameter
- **Prevention** — Pydantic validator with bleach.clean()
- **Pattern** — Remove HTML tags, keep alphanumeric + punctuation

#### Compound Low Scores
- **Scenario** — Both similarity < 0.4 AND rerank < 0.3
- **Decision** — Definite escalation (KB gap confirmed)
- **Formula** — 0.3×0.4 + 0.7×0.3 = 0.21 → escalate

### 9.7 Cross-Cutting Techniques

#### Instruction Hierarchy (IH)
- **Priority** — System > User > External Data
- **Implementation** — XML tags with salt (randomized)
- **Example** — `<SYSTEM_abc123>...system instruction...</SYSTEM_abc123>`
- **Meta-SecAlign** — Train model to prioritize system instructions

#### Security Scanner Node
- **Input** — Retrieved RAG documents
- **Detection** — Suspicious keywords (ignore, override, new instructions)
- **Action** — Escalate to security review if found
- **Benefit** — Blocks indirect prompt injection via documents

#### Least Privilege
- **Per-node API keys** — Limited scope
- **Rate limits** — Per-function throttling
- **Resource quotas** — Memory, token limits

---

## 10. Five Critical Technical Gaps

### 10.1 HITL State Management & Resume Flow

#### Checkpoint Persistence
- **HITLCheckpoint schema** — thread_id, node, state_snapshot, reason, approval_metadata
- **State recovery** — Restore full state on resume
- **Audit trail** — Who approved, when, what changed

#### Admin Workflow
- **List pending** — Query unapproved checkpoints
- **Approve endpoint** — Update approval_metadata, trigger resume
- **Reject handling** — Allow user to modify & retry

### 10.2 PostgreSQL Connection Pool Sizing

#### Pool Size Formula
- **Calculation** — (servers × users × queries/req × latency_sec) + buffer
- **SME example** — (2 × 100 × 3 × 0.2) + 2 = 122
- **Conservative range** — min=30-40, max=50-60

#### PostgreSQL 17 Config (SME 4GB RAM, 2-4 cores)
- **max_connections** — 150 (reserved 10 for admin)
- **shared_buffers** — 1GB (25% RAM)
- **effective_cache_size** — 3GB (75% RAM)
- **work_mem** — 4MB per connection
- **max_parallel_workers** — 4

#### Monitoring Queries
- **Connection count** — pg_stat_activity state != idle
- **Long-running queries** — Duration > 5 seconds
- **Pool exhaustion detection** — Alert on high utilization

### 10.3 Observability Stack Toggle

#### Environment Defaults
- **Dev** — Arize Phoenix (localhost:6006)
- **Staging** — Logfire or self-hosted Phoenix
- **Prod** — Logfire + LangSmith (optional)

#### Configuration Pattern
- **ENV variable** — OBSERVABILITY_BACKEND (phoenix/logfire/langsmith/none)
- **Fallback** — Python stdlib logging (always works)
- **Docker Compose** — depends_on: phoenix service

#### Key Metrics to Emit
- **service.name, agent.version, environment**
- **thread_id, user_id, timestamp**
- **escalation.reason, model.used, cost_usd**

### 10.4 FinOps: Cost Allocation per Team

#### Cost Tracking Schema
- **Tags** — team, project, cost_center, budget_quarter
- **Drivers** — llm_completion, embedding, reranker
- **Period** — Quarter-based budgets

#### Budget Enforcement
- **Warning threshold** — Alert approaching limit
- **Exceeded status** — Fallback to local model only
- **Automatic throttling** — Model downgrade on overspend

#### Reporting Queries
- **Weekly cost by team/project**
- **Spent vs budget tracking**
- **Cost driver breakdown**

### 10.5 PostgreSQL 17 Advanced Operations

#### Incremental VACUUM Strategy
- **Benefit** — No locks, 45min → 4min
- **Timing** — Daily 2 AM (off-peak)
- **Command** — VACUUM INCREMENTAL SKIP_LOCKED
- **Monitoring** — Track dirty page count

#### Bi-directional Index Scans (35% cost reduction)
- **Both directions** — Same index direction
- **Benefit** — Pagination queries (ASC & DESC)
- **Example** — ORDER BY created_at DESC

#### JSON_TABLE for State Audit
- **Native SQL queries** — Extract from JSONB directly
- **Use case** — State change audit
- **Benefit** — No app-level JSON parsing

#### Monitoring VACUUM
- **Query progress** — pg_stat_activity WHERE query LIKE '%VACUUM%'
- **Index sizes** — pg_indexes relation_size
- **Bloat detection** — Pages vs relation size ratio

---

## 11. Decision Matrices

### State Schema Choice
| Type | Speed | Validation | Use |
|------|-------|-----------|-----|
| TypedDict | Fast | No | Internal state |
| Pydantic | Medium | Yes | I/O boundaries |
| Dataclass | Fast | No | Simple, defaults |

### Reranker Selection
- **Dev** — Local CrossEncoder (0 VND, thread-based)
- **Prod** — API (Cohere > Jina > Voyage)

### Streaming Mode
- **Chat UI** — messages mode (typing effect)
- **Monitoring** — updates mode (deltas)
- **Full state** — values mode (snapshot)
- **Debug** — debug mode (trace)

### Confidence Threshold
- **Critical domain** — 0.75–0.85
- **SME sales** — 0.65–0.75 (default)
- **High KB coverage** — 0.55–0.65

### Escalation Trigger
- **Intent-first** — COMPLAINT/NEGOTIATION → escalate immediately
- **Score-based** — INFO_QUERY → escalate if score < threshold
- **Cost-based** — Escalate if token_cost > budget

---

## 12. Core Techniques (122 Shared Across Reports)

### Mandatory (Appear ≥3 reports)
- LangGraph Command API
- TypedDict + add_messages reducer
- Pydantic V2 validation
- AsyncPostgresSaver checkpointing
- interrupt() for HITL
- Intent-first escalation
- Confidence fusion
- LiteLLM router
- Escalation triggers
- Event loop safety

### Important (Appear 2-4 reports)
- Circuit breaker pattern
- Connection pool management
- anyio.to_thread.run_sync
- Fallback chains
- Tool injection prevention
- Message deduplication
- Schema drift detection
- Observability toggle

---

## 13. Definition of Done — All Tasks

| Task | Criteria |
|------|----------|
| 3.1 | TypedDict pure, serializable, reducers correct |
| 3.2 | All @tool async, no blocking calls, event loop safe |
| 3.3 | Pydantic schema for tool I/O, validation working |
| 3.4 | graph.compile() succeeds, Mermaid PNG exports correctly |
| 3.5 | Router classifies intent, intent-first escalation works |
| 3.6 | SSE streaming from FastAPI, run_id synced to logs |
| 3.7 | COMPLAINT/NEGOTIATION → premium, confidence < 0.65 → escalate |
| 3.8 | confidence = fusion(similarity, rerank), guardrail < 0.7 |
| 3.9 | pytest 100% pass, no secrets in logs |

---

**Format:** Technical Reference (No Code)  
**Version:** 1.0  
**Created:** 2026-03-02  
**Use:** Architecture decisions, planning, knowledge base  

See **techniques-overview.md** for full code examples and implementation details.
