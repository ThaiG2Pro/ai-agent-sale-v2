# 📜 PROJECT LOG: AI SALES AGENT (SME-READY) — (SME PRO / ZERO-COST FIRST)

## 🛠 WEEK 1: INFRA & TERMINAL CORE

> The AI Agent is an **I/O-bound** system (LLM + DB).  
> Async is **mandatory**, not an optimization.

| **No.** | **Task**                           | **Definition of Done**         | **Status**     |
| ------- | --------------------------------- | ------------------------------ | -------------- |
| 1.1     | `uv init`, Python 3.13+           | `uv sync` pass                 | [ ]            |
| 1.2     | Ruff config                       | `ruff check && format` pass    | [ ]            |
| 1.3     | Docker Postgres 17 + pgvector     | DB running via Compose         | [ ]            |
| 1.4     | AsyncEngine + async_sessionmaker  | Queries with await             | [ ]            |
| 1.5     | Declarative Models (Storage-only) | No SQLModel                    | [ ]            |
| 1.6     | FastAPI async endpoints           | `/health` < 10ms               | [ ]            |
| 1.7     | LiteLLM + Ollama test             | Offline chat 100%              | [ ]            |
| 1.8     | Async seed script                 | Does not block event loop     | [ ]            |
| 1.9     | Semantic Cache table + index      | Similarity search < 5ms (HNSW) | [ ]            |
| 1.10    | Structured Logging (JSON)         | Parseable logs (Logfire local) | [ ]            |
| 1.11    | Init /docs/adr/ and write ADR 001 | Tech selection + rationale     | [ ]            |
| 1.12    | RAG CLI (cli/rag_admin.py)        | Ingestion/index/search debug   | [ ]            |

### 🧠 Required DB design
Must include at least: `products`, `embeddings`, `semantic_cache`, `conversation_history`, `sales_signals`, `model_trace`. ❌ No Redis | ❌ No multi-DB
### 🔥 Week 1 principles
- Cache-first architecture
- Database as the single source of truth
- Strict async everywhere
- Do not hardcode model names
---

## 🧠 WEEK 2: VIETNAMESE RAG & EVALUATION

Goal this week: **correct and stable RAG**. Evaluation via plain Python scripts — no heavy frameworks.

| **No.** | **Task**                        | **Definition of Done**                          | **Status** |
| ------- | ----------------------------- | ---------------------------------------- | -------------- |
| 2.1     | Local `embed_text()`            | Text → vector (blocking allowed in Dev)         | [ ]            |
| 2.2     | Async vector search           | TopK result OK                           | [ ]            |
| 2.3     | Metadata enrichment           | ≥5 keywords extract                      | [ ]            |
| 2.4     | Query normalization (LiteLLM)   | Use Pydantic, No regex                          | [ ]            |
| 2.5     | Hybrid search (Vector + FTS)    | Recall > vector-only                             | [ ]            |
| 2.6     | Gold dataset (VN)               | ≥10 real-world queries (JSON)                   | [ ]            |
| 2.7     | RAG Flow v1                   | Rewrite → Search → Answer                | [ ]            |
| 2.8     | Evaluation CLI Runner           | CLI shows test cases for human grading (HITL)  | [ ]            |
| 2.9     | Adaptive TopK                   | TopK dynamic based on query length              | [ ]            |
| 2.10    | Similarity gap scoring          | Store score in state                            | [ ]            |
| 2.11    | Citation metadata mapping    | Return ProductID/ChunkID with answers    | [ ]            |
| 2.12    | Context compression          | Dedup/summarize chunks before LLM        | [ ]            |
| 2.13    | Confidence Threshold Guard      | If similarity < 0.7 → return "I couldn't find relevant information..." (avoid hallucination) | [ ]            |

**Logic Adaptive TopK:**
- Short query → TopK 5
- Long query → TopK 15
- Ambiguous → TopK 20
### ⚠ Embedding governance
- One environment = one embedding model
- Persist `embedding_model` + `dimension` in the DB
---

## 🤖 WEEK 3: AGENTIC WORKFLOW & SAFE LOGIC

**LangGraph is the orchestration core.** **LiteLLM + Pydantic** handle schema validation; do not introduce extra orchestration frameworks.
✅ MINDSET: **Agent = code + state machine** — stop relying on “clever prompts.”

| **No.** | **Task** | **Definition of Done** | **Status** |
|---|---|---|---|
|3.1|TypedDict State|Pure serializable|[ ]|
|3.2|Async tools|No synchronous calls (except local ML utilities)|[ ]|
|3.3|Pydantic tool schema|Strict input/output validation|[ ]|
|3.4|Graph compile + Mermaid|Export execution diagram|[ ]|
|3.5|Router node|Intent classification + intent-first escalation (escalate complaints/negotiations before RAG)|[ ]|
|3.6|Step-level streaming|Debuggable per-node streaming|[ ]|
|3.7|Model Escalation Node|Conditional upgrade logic|[ ]|
|3.8|Confidence scoring integration|Escalation decision based on scores|[ ]|
|3.9|Contract tests for Tools|Write contract tests for external tools (inventory/order) before implementing tool logic|[ ]|

Model Escalation Strategy (2026) — never default to a very large model:
if confidence < threshold:
    use_premium_model()
else:
    use_default_model()

---

## 🤝 WEEK 4: HUMAN-IN-THE-LOOP (HITL — BUSINESS CRITICAL)

> This is the project's strongest hiring differentiator. > **HITL is not a temporary fallback.**  
> HITL is a mandatory risk-control mechanism for revenue- or legally-sensitive actions.  
> LangGraph must pause & resume the flow — not FastAPI or the UI.

| **No.** | **Task detail**                         | **Definition of Done**                                                                                                                                 | **Status** |
| ------- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------- |
| **4.1** | `interrupt_before=["order_node"]`      | Graph pauses exactly before sensitive nodes (ordering/checkout). Do not perform side-effects without human approval.                                  | [ ]       |
| **4.2** | `graph.get_state()`                     | Admin can fetch **current state + next node** so they understand what the AI will do next without reading internal logs.                             | [ ]       |
| **4.3** | `/review` API (async)                   | Endpoint supports **Approve / Reject / Request Edit**. Async, idempotent, and safe to call multiple times.                                           | [ ]       |
| **4.4** | `update_state()`                        | Admin can edit the AI's planned output (e.g., reply content, order params) before resuming the graph; edits are recorded in state.                   | [ ]       |
| **4.5** | Confidence Guard (< 0.7 → Human)        | If confidence < threshold, force HITL — do not let the agent guess or answer. Confidence logic must be explicit (not prompt-based heuristics).       | [ ]       |
| 4.6     | Cost Guard                        | Token cost cao → Human                                                                                                                                                    |                |

✅ SME-realistic standards:
- HITL placed inside the graph (not patched into controllers)
- HITL occurs before side-effects (not after damage is done)
- Admin:
  - Sees the AI's next intended action
  - Can modify state, not just approve/reject
- Confidence Guard is a control mechanism, not prompt decoration

Cost Guard RULE:
`if estimated_token_cost > threshold:     escalate_to_human`

SMEs are extremely cost-sensitive.



---
## 💾 WEEK 5: ASYNC PERSISTENCE & MEMORY

| **No.** | **Task** | **Definition of Done** | **Status** |
|---|---|---|---|
|5.1|`AsyncPostgresSaver`|Conversation persisted across restarts|[ ]|
|5.2|Connection Pooling|Stable with < 20 connections|[ ]|
|5.3|Structured Summary|Conversation summaries stored in DB|[ ]|
|5.4|Semantic Memory (Vector)|Retrieve long-term past conversations|[ ]|
|5.5|Sales Intent Extraction|Extract fields: Budget, Urgency|[ ]|
|5.6|Intent tracking table|Persist customer intent/status|[ ]|


---

## 📱 WEEK 6: OMNICHANNEL & DOCKER PRO

SMEs care more about latency than TPS.

| **No.** | **Task** | **Definition of Done** | **Status** |
|---|---|---|---|
|6.1|Async Telegram bot|Low-latency, non-blocking|[ ]|
|6.2|Webhook security|Verify signature Telegram|[ ]|
|6.3|Timeout guard tools|Tools cut off after 5s|[ ]|
|6.4|Multi-stage Docker|Optimized image size (< 300MB base)|[ ]|
|6.5|Compose prod config|Healthcheck, Restart policy|[ ]|

---

## 🛡 WEEK 7: SAFETY & OBSERVABILITY

This week turns the system into a self-optimizing one.

| No. | Task                              | Definition of Done              | Status |
| --- | --------------------------------- | ------------------------------- | ---------- |
| 7.1 | Semantic cache logic              | Hit rate > 30%                  | [ ]        |
| 7.2 | Cache similarity threshold tuning | Timeout for cache queries       | [ ]        |
| 7.3 | Rerank conditional activation     | **Dev: Local or Prod: API**     | [ ]        |
| 7.4 | LangSmith tracing                 | Toggleable via ENV              | [ ]        |
| 7.5 | Load test (Locust)                | Basic load resilience test      | [ ]        |
| 7.6 | Rate limiting                     | Prevent request spam            | [ ]        |
| 7.7 | Metrics dashboard                 | Cache %, escalation %, rerank % | [ ]        |
### 📊 Required metrics to monitor
- Cache hit rate
- Escalation frequency
- Avg tokens per response
- Rerank activation %
- Confidence distribution
- Avg latency
---

## 🚀 WEEK 8: PORTFOLIO & RECRUITMENT

> This week **no new features** — focus on packaging value.

| **No.** | **Task** | **Definition of Done** | **Status** |
|---|---|---|---|
|8.1|Architecture diagram|Complete README|[ ]|
|8.2|Demo video|Record HITL & escalation demo|[ ]|
|8.3|Tech blog|Write about "Lean AI Architecture"|[ ]|
|8.4|Cost projection|Realistic cost estimate|[ ]|
|8.5|Clean Code|Final refactor|[ ]|

---
# 🏆 FINAL ENGINEERING PRINCIPLES (2026)

1. **Cache First:** Always check cache before calling the LLM.
2. **Layered Intelligence:** Use cheap models first, smarter models only when needed.
3. **Lean Architecture:** No Redis, no unnecessary frameworks.
4. **Single Truth:** All state lives in Postgres.
5. **Zero-Cost Dev:** Must run fully offline.

---

# 🎯 What this project proves you can do

- Build production-ready async backends
- Design agentic orchestration
- Implement RAG + hybrid retrieval
- Handle SME constraints
- Apply cost-aware AI design (2026)
- Deliver controllable, self-optimizing AI systems