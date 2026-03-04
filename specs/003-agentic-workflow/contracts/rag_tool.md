# Contract: RAG Search Tool

**Tool**: `rag_search`  
**Location**: `core/agent/tools.py`  
**Wraps**: `services.rag.pipeline.answer_with_rag(db, query, model)`  
**Contract Tests**: `tests/contract/tools/test_rag_tool_contract.py`  
**DB Session**: Tool is built as a factory closure `make_rag_tool(db: AsyncSession)` — see data-model.md §7 for injection pattern.

---

## Input Schema: RAGSearchInput

```
RAGSearchInput (Pydantic BaseModel, strict mode)
├── query: str
│   Constraints: min_length=1, max_length=2000
│   Description: "The user's question or search query in natural language"
├── session_id: str
│   Constraints: pattern=r"^[0-9a-f-]{36}$" (UUID format)
│   Description: "Active session identifier for context linkage"
└── model: str
    Default: "economy-chat"
    Constraints: pattern=r"^[a-z0-9-]+$"
    Description: "LiteLLM model alias to use for answer generation"
```

**Validation rules**:
- Empty or whitespace-only `query` → rejected at schema level (min_length=1 after strip)
- `session_id` must be valid UUID format — prevents injection via session context
- `model` must match registered alias pattern — no arbitrary model names

---

## Output Schema: RAGSearchOutput

```
RAGSearchOutput (Pydantic BaseModel, strict mode)
├── answer: str                   # Final answer text (may be DECLINE_MESSAGE)
├── declined: bool                # True if Layer 1 guard (similarity < 0.45) fired
├── citations: list[CitationItem] # Empty list if declined
├── similarity_score: float       # Best cosine score from pgvector (0.0–1.0)
├── confidence_score: float       # Same as similarity_score in this tool
│                                 # (agent confidence_node applies fusion separately)
├── model_used: str               # "economy-chat", "premium-local-chat", or "cache"
└── chunks_used: int              # Number of chunks after compression

CitationItem
├── product_id: str   # UUID string (UUIDv7)
├── chunk_id: str     # UUID string (UUIDv7)
├── sku: str
└── name: str
```

> **Note on `escalation_flag`**: `RAGResult.escalation_flag` in the Week 2 pipeline is **always `False`** — the RAG tool never performs model escalation internally. The agent-level `escalation_flag` in `AgentState` is set exclusively by the `escalation_node` (based on intent classification), not by this tool. `RAGSearchOutput` deliberately omits `escalation_flag` to prevent confusion.

---

## Required Test Scenarios

### Scenario 1 — 200 OK: Valid response with citations
- **Input**: Valid `RAGSearchInput` with known product query
- **Mock**: LLM returns structured answer; vector search returns 3 chunks with similarity ≥ 0.8
- **Assert**: Output matches `RAGSearchOutput` schema. `declined=False`. `len(citations) >= 1`. `model_used` is non-empty string.

### Scenario 2 — Layer 1 Guard: Declined without LLM call (similarity < 0.45)
- **Input**: Valid query, mock returns similarity_score=0.40 (below Layer 1 threshold of 0.45)
- **Mock**: Vector search returns low-score chunks; no LLM call expected
- **Assert**: `declined=True`. `answer == DECLINE_MESSAGE` (from `services/rag/constants.DECLINE_MESSAGE`). `model_used` is not a premium model string. LLM was NOT called (assert via respx call count = 0).

### Scenario 3 — LLM 429 Too Many Requests
- **Input**: Valid query
- **Mock**: respx returns HTTP 429 on first call
- **Assert**: Tool handles gracefully (no exception propagates). Response is either a fallback message or the error is wrapped in `RAGSearchOutput.answer`. `declined=True`.

### Scenario 4 — LLM 500 Server Error
- **Input**: Valid query
- **Mock**: respx returns HTTP 500
- **Assert**: Graceful degradation. No unhandled exception. `declined=True`.

### Scenario 5 — ReadTimeout
- **Input**: Valid query
- **Mock**: `respx.side_effect = httpx.ConnectTimeout`
- **Assert**: Tool returns within 10s (timeout guard). `declined=True`. No exception propagates to caller.

---

## Schema Drift Detection

A baseline snapshot of `RAGSearchOutput` field names and types is stored in `tests/contract/tools/baselines/rag_tool_baseline.json`. On every test run, the contract test performs a structural diff (field names + types, not values) against the baseline. Any removal or type change in `RAGSearchOutput` causes the contract test to fail.
