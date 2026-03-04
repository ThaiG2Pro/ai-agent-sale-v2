# Quickstart: Agentic Workflow & Safe Logic (Week 3)

**Branch**: `003-agentic-workflow`  
**Prerequisite**: Week 1 (infra + DB) and Week 2 (RAG pipeline) must be complete.

---

## Prerequisites Check

```bash
# 1. Verify you're on the correct branch
git branch --show-current
# Expected: 003-agentic-workflow

# 2. Verify database is running
docker compose ps
# Expected: postgres service = running/healthy

# 3. Verify Ollama is available (dev only)
curl http://localhost:11434/api/tags | python -m json.tool | grep -E "qwen3|bge-m3"
# Expected: at least qwen3 and bge-m3 listed

# 4. Verify Week 2 RAG tests still pass
uv run pytest tests/ -x -q --ignore=tests/integration/test_rag.py
# (run integration tests separately if DB is available)
```

---

## Install New Dependencies

```bash
# Week 3 adds: langgraph, pytest-asyncio (if not already), respx
uv add "langgraph>=0.3" "respx>=0.21"

# Verify
uv run python -c "import langgraph; print(langgraph.__version__)"
```

---

## Run Contract Tests (Phase 0 — before implementation)

```bash
# Contract tests MUST be written first (Article IV / FR-009)
# Run to confirm they fail before implementation (TDD Red phase)
uv run pytest tests/contract/ -v
# Expected: ALL FAIL (not yet implemented)
```

---

## Run the Debug CLI (once agent is implemented)

```bash
# Single-turn offline test (MemorySaver, Ollama models)
uv run python cli/run_agent.py "Giá sản phẩm X là bao nhiêu?"

# With verbose streaming (see each node as it fires)
uv run python cli/run_agent.py --stream "Tôi muốn khiếu nại về đơn hàng"

# With session ID (for multi-turn context)
uv run python cli/run_agent.py --session "test-session-001" "Còn hàng không?"
```

**Expected output (streaming)**:
```
[router_node] intent=INFO_QUERY confidence=0.94
[retrieval_node] retrieved=5 chunks, best_similarity=0.87
[confidence_node] confidence_score=0.87 → ACCEPTED
[answer_node] model=economy-chat
─────────────────────────────────────────────────────
Sản phẩm X có giá 250,000 VND. [Nguồn: SKU-001]
```

**Escalation example**:
```
[router_node] intent=COMPLAINT confidence=0.91
[escalation_node] escalate=True reason=intent_escalation model=premium-local-chat
[answer_node] model=premium-local-chat
```

---

## Run All Week 3 Tests

```bash
# Unit tests (deterministic, no DB required)
uv run pytest tests/unit/ -v -k "agent or router or confidence or escalation"

# Contract tests (respx mocks, no real HTTP)
uv run pytest tests/contract/ -v

# Integration test (requires DB + Ollama)
uv run pytest tests/integration/test_agent_flow.py -v

# Full suite
uv run pytest tests/ -v --tb=short
```

---

## Export Mermaid Graph Diagram

```bash
# Export to file
uv run python -c "
from core.agent.graph import build_graph
g = build_graph()
diagram = g.get_graph().draw_mermaid()
with open('docs/week3/agent-graph.mmd', 'w') as f:
    f.write(diagram)
print('Exported to docs/week3/agent-graph.mmd')
"
```

---

## Validate Constitution Compliance

```bash
# No blocking calls in event loop
uv run ruff check core/agent/ --select ASYNC

# No regex on LLM output
grep -rn "re\.\(match\|search\|findall\)" core/agent/ && echo "VIOLATION" || echo "CLEAN"

# No direct SDK imports (only litellm allowed)
grep -rn "^import openai\|^import anthropic\|^from openai\|^from anthropic" core/agent/ && echo "VIOLATION" || echo "CLEAN"
```

---

## Environment Variables (Week 3 additions to .env)

```bash
# Optional: enable reranker (disabled by default in dev)
RERANKER_ENABLED=false

# Optional: set agent recursion limit (default: 5 per Article X)
AGENT_MAX_TURNS=5

# Optional: checkpointer backend (dev uses MemorySaver by default)
# ENVIRONMENT=prod  # uncomment to use AsyncPostgresSaver
```

---

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| `ImportError: langgraph` | Not installed | `uv add "langgraph>=0.3"` |
| `Router node hangs` | Ollama model not loaded | `ollama pull qwen3:0.6b` |
| `confidence_score=0.0` | Embedding service down | Check `ollama pull bge-m3` |
| `Contract tests pass before impl` | Test logic wrong | Tests should FAIL before impl (TDD Red) |
| `Mermaid export empty` | Graph not compiled | Ensure `graph.compile()` called in `build_graph()` |
| `OOM / slow model swap` | Router using light-chat then economy-chat | Router MUST use economy-chat — see research Decision 10 (Ollama G1) |
| `declined=True from RAG but agent still called LLM` | Missing Layer 1 propagation | confidence_node must fast-path when `rag_result.declined=True` |
| `similarity=0.55 → agent returns fallback` | Expected — dual-layer guard | Layer 1=0.45 passed, Layer 2=0.70 failed (fused < 0.70). This is correct behavior. |
