# AI Coding Agent Instructions (SME Pro 2026 Optimized)

You are an expert AI software engineer building an **SME-Ready AI Sales Agent (2026)**.
This system must demonstrate:
- Engineering maturity    
- Cost intelligence    
- Adaptive orchestration    
- Production realism    
- Zero-cost-first philosophy    
``
This is not a “model demo project”.  
This is a **lean AI sales machine**.

---
## 1. The Big Picture & Architecture
### Core Philosophy
- **LEAN & SME-REALISTIC:** No over-engineering. No K8s. No Redis. No Celery.
- **ZERO-COST-FIRST:** Must fully operate at 0 VND locally.
- **LOCAL-FIRST:** Ollama + Postgres before any cloud dependency.
- **SINGLE-DB ARCHITECTURE:** PostgreSQL is the only database.
- **ADAPTIVE > STATIC:** Routing, reranking, model selection must be dynamic.
- **CACHE-FIRST STRATEGY:** Reduce model calls before scaling models.
- **ESCALATE ONLY WHEN NECESSARY:** Intelligence is layered, not default.
---
### System Flow (2026 Adaptive)
Telegram User  
→ FastAPI Webhook  
→ Semantic Cache Check  
→ LiteLLM Proxy  
→ LangGraph Agent  
→ Hybrid Retrieval (Vector + FTS)  
→ Conditional Rerank  
→ Context Compression  
→ Model Escalation (if needed)  
→ HITL (Critical Actions)  
→ Response  
→ Store Embedding + Trace

---
### Architecture Layers
1. Interface Layer – Telegram + FastAPI
2. Intelligence Gateway – LiteLLM
3. Orchestration Layer – LangGraph
4. Retrieval Layer – Postgres + pgvector
5. Optimization Layer – Cache + Adaptive Rerank + Model Escalation
6. Human Safety Layer – HITL
7. Observability Layer – Structured Logging + Optional LangSmith
---
## 2. Tech Stack (Strict & 2026 Lean)
### Runtime & Core
- Python 3.13+
- Package Manager: `uv`
- FastAPI (Async)
- Uvicorn
---

### Agent & Orchestration
- LangGraph (mandatory)
- Chỉ dùng LiteLLM với tham số `response_format` trỏ vào Pydantic model thuần (không dùng Pydantic AI framework).

No CrewAI  , No AutoGen  , No Pydantic AI (Redundant with LangGraph + LiteLLM)) , No regex parsing hacks

---
Testing & Eval:
    - pytest: For unit/integration tests.
    - Custom Script: For AI evaluation (loop + LLM grading). No complex eval frameworks (like Ragas) initially.

---
### Model Gateway (MANDATORY)
- LiteLLM only    
    - No direct SDK imports        
    - All models config-based        
    - Must support local → API → premium escalation
    - cập nhật lên bản mới nhất để có tính năng **Retries with Exponential Backoff** mặc định mà không cần viết thêm logic tay.
Allowed calls:
```python
litellm.completion()
litellm.acompletion()
litellm.embedding()
```

---
### Database (Single-DB Principle)
- PostgreSQL 17+
- pgvector 0.8+  (hỗ trợ **Streaming Index Builds** và tối ưu cực mạnh cho bộ nhớ khi chạy trên Postgres 17.  giúp bảng `embeddings`  tra cứu nhanh hơn 20-30% so với các bản cũ.)
- SQLAlchemy 2.0 (Async) (`sqlalchemy[asyncio]>=2.0.30`.)
- asyncpg
-- **Custom Postgres Decorator** (Replaces fastapi-cache2 for stateless, DB-backed semantic caching)

### Removed / Banned
❌ fastapi-cache2 (Violates stateless/no-redis rule)
❌ ORM Lazy Loading

 No Pinecone  , No Weaviate  , No Qdrant
---
### RAG & Ingestion
- PyMuPDF (default)
- Crawl4AI (dùng bản mới nhất để tận dụng tính năng **CSS-based filtering** ngay lúc cào, giúp giảm lượng token rác gửi vào LLM, tiết kiệm chi phí cho con Agent.)
- Llama Parse (optional, paid)
Hybrid Retrieval:
- Vector search    
- PostgreSQL FTS    
- Combined SQL ranking   

---
#### Gợi ý decorator thủ công (L1/L2):
```python
def semantic_cache_decorator(func):
    async def wrapper(query: str, *args, **kwargs):
        # L1: exact-match via SHA256 hash
        qnorm = query.strip().lower()
        qhash = sha256(qnorm.encode()).hexdigest()
        row = await db.fetchrow("SELECT response FROM semantic_cache WHERE query_hash=$1", qhash)
        if row:
            return row["response"]
        # L2: semantic fallback
        emb = await litellm.embedding(qnorm)
        sem = await pgvector_search(emb)
        if sem and sem.similarity > 0.95:
            return sem.response
        # Miss: call the function and persist both hash and embedding
        resp = await func(query, *args, **kwargs)
        await db.execute("INSERT INTO semantic_cache (query_hash, query_text, response, embedding, created_at) VALUES ($1,$2,$3,$4, now())",
                         qhash, qnorm, resp, emb)
        return resp
    return wrapper
```

---
### Embeddings (2026 Rule)
- One embedding model per environment
- Store model name + version in DB
- Never mix embeddings

Dimension must remain consistent within environment staging and product..

---
### Reranking (Adaptive Strategy)
Reranking is not always-on.
Flow:
```
Hybrid Search Top-K
→ If similarity gap < threshold
      → Rerank
→ Else
      → Skip rerank
```
Dev: Local lightweight reranker
Staging / Prod: API reranker (Cohere, Jina, Voyage, etc.),  Cheapest viable option first.

Blocking Policy:
    - Dev/Local: Local Reranker (HuggingFace CrossEncoder) is allowed for development, BUT must be executed off the event loop using `anyio.to_thread.run_sync` or `loop.run_in_executor` to avoid blocking FastAPI's event loop.
    - Prod/Staging: MUST use Async Rerank APIs (Cohere/Jina/Voyage) to ensure non-blocking performance without complex threading code.

Rationale: Article V mandates non-blocking I/O. Running CPU-bound rerankers directly in the event loop will freeze the server; offloading to threads preserves responsiveness and prevents hard-to-debug latency bugs when moving from Dev to Prod.

---
### Observability

Minimalist-first:
- **OpenTelemetry (OTLP gateway)** — protocol-first observability with an OTLP gateway to enable flexible backend routing.
    - Local Dev: self-hosted Arize Phoenix (offline-first) for trace/debug and evaluation.
    - Production/Staging: optional Logfire (cloud) for deep Python monitoring; LangSmith for LangGraph-specific traces if needed.
- Python standard logging (Stdout/JSON) as a guaranteed fallback.

---
### Deployment
Docker + Docker Compose only
Services:
- app    
- postgres
No extra infra.

tham khảo : dùng image nền là `python:3.13-slim-bookworm`.

---

## 3. Model & Cost Policy (Adaptive 2026)
### Environment Defaults
---

#### Dev (0 VND – Offline)

```env
CHAT_MODEL=ollama/qwen2.5-3b-instruct-q4 
EMBED_MODEL=bge-small or bge-m3 (Vietnamese)
RERANK_MODE=local_lightweight/local_heavy
TOPK_BASE=8
MODEL_ESCALATION=false
CACHE_ENABLED=true
```

Purpose:
- Logic validation    
- RAG testing    
- Zero cost proof   
---
#### Staging (Low Cost + Realistic)
```env
CHAT_MODEL=groq/llama-3.1-8b or openrouter/cheap
EMBED_MODEL=openai/text-embedding-3-small (1536 dim) or keep 1024 dim like bge-m3
RERANK_MODE=api
TOPK_BASE=15
MODEL_ESCALATION=true
CACHE_ENABLED=true
```

---

#### Production (SME Optimized)
```env
CHAT_MODEL=groq/llama-3.1-8b
EMBED_MODEL=1536-dimension model or 1024-dimesion model (same as staging)
RERANK_MODE=api
TOPK_BASE=15
MODEL_ESCALATION=conditional
CACHE_ENABLED=true
```

---

### Model Escalation Logic (New 2026 Layer)
Do NOT default to a large model. Escalation must consider both *intent* and *confidence/score*.

Flow:
1. Run a cheap intent classifier (cheap model) first.
2. If Intent in {COMPLAINT, NEGOTIATION}: escalate immediately to a Premium model (human-facing or sensitive conversations).
3. Else if Intent == INFO_QUERY: use similarity_score/confidence to decide whether to escalate for summarization or deeper reasoning.
4. Otherwise use the default (economy) model.

Pseudo:

```
intent = cheap_model.classify(query)
if intent in ("COMPLAINT", "NEGOTIATION"):
    use_premium_model()
elif intent == "INFO_QUERY" and confidence_score < threshold:
    use_premium_model()
else:
    use_default_model()
```

Note: This prevents dangerous shortcuts where a high similarity to policy text (high score) would keep the cheap model in control during crisis/negotiation scenarios. Intent-first escalation aligns with safety and the Constitution's Article V/Article VIII requirements.

---

## 4. Optimization Layer (NEW – Mandatory 2026)

### A. Semantic Response Cache
Before calling LLM:
1. Embed user query    
2. Search semantic_cache    
3. If similarity > 0.95 → return cached answer 

Reduces cost up to 70%.

Layered cache (L1 / L2):

- L1 — Exact Match (Hash-based):
    - Compute a canonicalized query string (trim, normalize case/whitespace) and a SHA-256 hash.
    - Query Postgres for an exact `query_hash` match in the `semantic_cache` table (O(1)).
    - If found, return cached answer immediately — NO embedding call, zero token cost.

- L2 — Semantic Match (Vector):
    - Only on L1 miss: call the embedding model, then perform a pgvector search (O(n)).
    - Apply similarity threshold (e.g., 0.95) and return cached semantic answer if matched.

Why: many SME interactions are identical short queries ("Price of product A?"). L1 avoids needless embedding calls and keeps repeated exact-queries effectively zero-cost for the customer.

Operational notes:
- Store both `query_hash` (sha256), `query_text` (canonical), `embedding` (optional), `response`, `model_name`, `similarity`, `created_at` in `semantic_cache`.
- On write/update to product data, invalidate affected hash entries and/or set TTL for semantic entries.

---

### B. Context Compression
Before sending to LLM:
- Deduplicate chunks    
- Summarize repetitive info    
- Remove low-signal text
Goal:  
Reduce token usage by 20–40%.

---

### C. Adaptive TopK
TopK must not be static.
Example:
- Short query → 5
- Long query → 15
- Ambiguous query → 20
---

### D. Confidence Scoring
Each answer must store:
- similarity score    
- rerank score    
- model used    
- escalation flag

This allows real optimization later.

---

## 5. Agent & LangGraph Rules
### A. State Management
Typed state only:
```python
Annotated[list, add_messages]
```
No global mutable state.

---
### B. Structured Outputs
Pydantic models only.
No JSON string parsing.
No regex extraction.
Avoid redundant abstraction layers for structured LLM outputs.

---

### C. Human-in-the-Loop (HITL)
Mandatory for:
- Checkout    
- Final pricing    
- Order confirmation    
- Refunds    

Use:

```python
interrupt_before=["checkout_node"]
```

---

## 6. RAG Strategy (SME-Pro 2026)
### Retrieval Flow

```
Vector Search (TopK dynamic)
+ FTS ranking
→ Hybrid Merge
→ Conditional Rerank
→ Top 5 Context
→ Context Compression
→ LLM
```

---

### Embedding Governance
Each embedding record must store:
- embedding_model
- version
- dimension
Never re-use DB across different embedding dimensions.

---

## 7. FastAPI & Async Rules
- Strict async
- Use httpx
- No blocking calls
- Domain-based folders
- BackgroundTasks only

No Celery.

---

## 8. Known Pitfalls (Updated 2026)

❌ No static TopK  
❌ No always-on reranker  
❌ No large model default  
❌ No vendor SDK imports  
❌ No multi-database  
❌ No skipping cache  
❌ No premature optimization

---

## 9. Deployment Rules

Docker Compose only.

`restart: always`

Environment-based configuration only.

No hardcoded secrets.

---

## FINAL PRINCIPLE (2026 Edition)

> Intelligence is layered, not default.  
> Cache first.  
> Escalate second.  
> Scale last.

If it runs at 0$ and converts sales →  
It will dominate when funded.

## Active Technologies
- Python 3.13+ (Mandatory) + FastAPI (Async), LiteLLM, Ollama (Local), SQLAlchemy 2.0 (Async), OpenTelemetry (OTLP), ruff (001-project-infra-setup)
- PostgreSQL 17 + pgvector 0.8+ (001-project-infra-setup)
- Python 3.13+ + FastAPI, LiteLLM, Ollama, SQLAlchemy 2.0 (async), Alembic, Pydantic, OpenTelemetry (OTLP), ruff (001-project-infra-setup)
- PostgreSQL 17 + pgvector 0.8+ (organized in a dedicated schema) (001-project-infra-setup)
- PostgreSQL 17 + pgvector 0.8+ (organized in a dedicated schema `agent_v1`) (001-project-infra-setup)

## Recent Changes
- 001-project-infra-setup: Added Python 3.13+ (Mandatory) + FastAPI (Async), LiteLLM, Ollama (Local), SQLAlchemy 2.0 (Async), OpenTelemetry (OTLP), ruff
