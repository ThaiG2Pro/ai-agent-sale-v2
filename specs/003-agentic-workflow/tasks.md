# Tasks: Agentic Workflow & Safe Logic (Week 3)

**Feature**: `003-agentic-workflow`  
**Input**: `plan.md`, `spec.md`, `data-model.md`, `contracts/rag_tool.md`, `contracts/inventory_tool.md`, `research.md`, `quickstart.md`  
**Branch**: `003-agentic-workflow`

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Parallelizable — no dependency on other incomplete tasks in this phase
- **[Story]**: User story label (US1–US5) for story-scoped phases
- **Keywords** + **Boilerplate hints** provided per task for developer reference

---

## Phase 1: Setup (Project Initialization)

**Purpose**: Add new dependencies, scaffold directory tree, environment variables, and ADR document. No business logic yet.

- [x] T001 Verify `langgraph>=0.3` is in `pyproject.toml` — check `uv run python -c "import langgraph; print(langgraph.__version__)"` in terminal. If missing, run `uv add "langgraph>=0.3"`. **Keywords**: `uv add`, `langgraph.__version__`

- [x] T002 Add `respx>=0.21` dev dependency for HTTP transport-layer mocking in contract tests — run `uv add --dev "respx>=0.21" "pytest-asyncio>=0.23"`. Verify with `uv run python -c "import respx; print(respx.__version__)"`. **Keywords**: `respx`, `pytest-asyncio`, `uv add --dev`

- [x] T003 [P] Create `core/agent/` package with `__init__.py` and stub docstring. File: `core/agent/__init__.py`. **Boilerplate**: `"""Agent orchestration package — LangGraph StateGraph for AI Sales Agent."""`

- [x] T004 [P] Create `core/agent/nodes/` sub-package with `__init__.py`. File: `core/agent/nodes/__init__.py`. **Boilerplate**: `"""LangGraph node implementations for the sales agent graph."""`

- [x] T005 [P] Create `tests/contract/` package structure: `tests/contract/__init__.py` and `tests/contract/tools/__init__.py` (both empty files). Needed for pytest discovery. **Keywords**: `pytest`, `__init__.py`, package discovery

- [x] T006 [P] Create `tests/contract/tools/baselines/` directory with `.gitkeep`. This directory holds JSON snapshot files for schema drift detection. `mkdir -p tests/contract/tools/baselines && touch tests/contract/tools/baselines/.gitkeep`

- [x] T007 [P] Create `tests/unit/` directory if not exists, add `tests/unit/__init__.py`. Run `ls tests/unit/` to verify. **Keywords**: unit test layout

- [x] T008 [P] Add Week 3 environment variables to `.env.example` (not `.env`): `RERANKER_ENABLED=false`, `AGENT_MAX_TURNS=5`, `AGENT_CONFIDENCE_THRESHOLD=0.70`, `AGENT_ALPHA=0.7`. **Keywords**: `pydantic-settings`, environment config, no hardcoded secrets

- [x] T009 Add new settings fields to `core/config.py` with Pydantic validation constraints: `LAYER1_CONFIDENCE_THRESHOLD: float = Field(default=0.45, ge=0.0, le=0.6)`, `RERANKER_ENABLED: bool = False`, `AGENT_MAX_TURNS: int = Field(default=5, ge=1, le=20)`, `AGENT_CONFIDENCE_THRESHOLD: float = Field(default=0.70, gt=0.45, le=1.0)` (must be strictly > L1 threshold via `gt=0.45` constraint), `AGENT_ALPHA: float = Field(default=0.7, ge=0.0, le=1.0)`. **Boilerplate**: add fields inside `Settings` class next to AI Configuration section. Pydantic will raise `ValidationError` at startup if misconfigured. **Note**: LAYER1_CONFIDENCE_THRESHOLD is the new constant — ALL retrieval layer references should use `settings.LAYER1_CONFIDENCE_THRESHOLD` instead of hardcoded 0.45. **Keywords**: shared constants, validation constraints, dual-layer ordering

- [x] T010 [P] Create `docs/week3/` directory with `.gitkeep` for Mermaid export artifact. Run `mkdir -p docs/week3 && touch docs/week3/.gitkeep`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Define all shared types — enums, TypedDict state, and Pydantic boundary models — that every node and test depends on. No graph logic yet.

**⚠️ CRITICAL**: No user story work (nodes, tools, tests) should begin until T011–T024 are complete.

- [x] T011 Create `core/agent/state.py` skeleton with module docstring and imports. Add `from __future__ import annotations`, imports for `typing`, `operator`, `langgraph.graph.message`, `pydantic`, `enum`. **Boilerplate**:
  ```python
  """
  Why this exists: Defines canonical AgentState TypedDict and all Pydantic
  boundary models (Article VI) for the LangGraph sales agent.
  What it does: Provides typed state, enums, and I/O contracts used by all nodes.
  """
  from __future__ import annotations
  import operator
  from enum import Enum
  from typing import Annotated, Any
  from langgraph.graph.message import add_messages
  from pydantic import BaseModel, Field
  from typing_extensions import TypedDict
  ```

- [x] T012 Define `IntentEnum` in `core/agent/state.py` with all 7 values: `INFO_QUERY`, `PRICING`, `COMPARISON`, `COMPLAINT`, `NEGOTIATION`, `SMALLTALK`, `AVAILABILITY`. **Boilerplate**:
  ```python
  class IntentEnum(str, Enum):
      INFO_QUERY = "INFO_QUERY"
      PRICING = "PRICING"
      COMPARISON = "COMPARISON"
      COMPLAINT = "COMPLAINT"
      NEGOTIATION = "NEGOTIATION"
      SMALLTALK = "SMALLTALK"
      AVAILABILITY = "AVAILABILITY"
  ```

- [x] T013 Define `EscalationReasonEnum` in `core/agent/state.py` with 3 values: `intent_escalation`, `low_confidence`, `none`. **Boilerplate**:
  ```python
  class EscalationReasonEnum(str, Enum):
      INTENT_ESCALATION = "intent_escalation"
      LOW_CONFIDENCE = "low_confidence"
      NONE = "none"
  ```

- [x] T014 Define `Citation` Pydantic model in `core/agent/state.py`. Fields: `product_id: str`, `chunk_id: str`, `sku: str`, `name: str`, `source_text: str` (the raw chunk text used for grounding, required by spec Key Entities and Article IX for RAG auditability). Add `model_config = ConfigDict(strict=True)`. **Keywords**: `Pydantic BaseModel`, `strict mode`, `source_text`, Article IX auditability

- [x] T015 Define `IntentClassification` Pydantic model in `core/agent/state.py`. Use `primary_intent` + `secondary_intents` to support FR-005/FR-007 multi-intent detection. Fields: `primary_intent: IntentEnum`, `secondary_intents: list[IntentEnum] = Field(default_factory=list)`, `confidence: float = Field(ge=0.0, le=1.0)`, `reasoning: str`. Routing uses `primary_intent`; escalation logic checks `primary_intent` AND all `secondary_intents` for `COMPLAINT`/`NEGOTIATION` (FR-007: "if ANY detected intent"). **Boilerplate**:
  ```python
  class IntentClassification(BaseModel):
      primary_intent: IntentEnum
      secondary_intents: list[IntentEnum] = Field(default_factory=list)
      confidence: float = Field(ge=0.0, le=1.0)
      reasoning: str

      def has_escalation_intent(self) -> bool:
          """True if ANY intent (primary or secondary) is COMPLAINT or NEGOTIATION."""
          escalation = {IntentEnum.COMPLAINT, IntentEnum.NEGOTIATION}
          return self.primary_intent in escalation or bool(escalation & set(self.secondary_intents))
  ```
  **Keywords**: `response_format`, multi-intent, `has_escalation_intent`, FR-007 "any detected intent"

- [x] T016 Define `EscalationDecision` Pydantic model in `core/agent/state.py`. Fields: `escalate: bool`, `reason: EscalationReasonEnum`, `selected_model: str`. **Keywords**: escalation logic, model alias, pure Python node output

- [x] T016b Define `TraceMetadata` Pydantic model in `core/agent/state.py` to structure `model_trace.metadata_` JSONB schema. Fields: `guard_decision: str` (ACCEPTED|REJECTED), `escalation_reason: str | None` (from `EscalationReasonEnum`), `escalation_failure: bool`, `escalation_flag: bool`, `intended_model: str | None` (the model selected/attempted, even if not executed due to decline), `declined: bool`, `similarity_score: float`, `confidence_score: float`. Add `model_config = ConfigDict(extra="allow")` to permit future instrumentation fields. **Keywords**: trace metadata structure, JSONB schema definition, audit trail

- [x] T017 Define `AgentState` TypedDict in `core/agent/state.py` with all fields from data-model.md §1. Use `Annotated[list, add_messages]` for `messages` and `Annotated[list, operator.add]` for `citations`. **Boilerplate**:
  ```python
  class AgentState(TypedDict):
      session_id: str
      user_message: str
      messages: Annotated[list, add_messages]  # conversation history — NOT "conversation_history"
      intent: str | None
      secondary_intents: list[str]              # additional detected intents (default=[])
      intent_confidence: float
      retrieved_chunks: list[dict]
      citations: Annotated[list, operator.add]
      similarity_score: float
      rerank_score: float | None
      confidence_score: float
      model_used: str | None
      escalation_flag: bool
      escalation_reason: EscalationReasonEnum | None  # use Enum, not raw str
      escalation_failure: bool          # True if premium unavailable → economy fallback
      response: str | None
      declined: bool
      error: str | None
  ```

- [x] T018 [P] Define `RAGSearchInput` Pydantic model in `core/agent/tools.py` (create file). Fields: `query: str = Field(min_length=1, max_length=2000)`, `session_id: str = Field(pattern=r"^[0-9a-f-]{36}$")`, `model: str = Field(default="economy-chat", pattern=r"^[a-z0-9-]+$")`. Add strict mode. **Keywords**: `strict=True`, `Field(min_length=)`, Pydantic v2 validation

- [x] T019 [P] Define `CitationItem` (tool-level) and `RAGSearchOutput` Pydantic models in `core/agent/tools.py`. `RAGSearchOutput` fields: `answer: str`, `declined: bool`, `citations: list[CitationItem]`, `similarity_score: float`, `confidence_score: float`, `model_used: str`, `chunks_used: int`. Add `from_rag_result` classmethod stub. **Keywords**: `@classmethod`, `from_rag_result`, `RAGResult` bridge

- [x] T020 [P] Define `InventoryLookupInput` Pydantic model in `core/agent/tools.py`. Fields: `sku: str = Field(min_length=1, max_length=50, pattern=r"^[A-Z0-9_-]+$")`, `warehouse_id: str | None = Field(default=None, pattern=r"^[A-Z0-9]{3,10}$")`. **Keywords**: `pattern=`, injection prevention, strict Pydantic

- [x] T021 [P] Define `InventoryLookupOutput` Pydantic model in `core/agent/tools.py`. Fields: `sku: str`, `stock_level: int = Field(ge=0)`, `warehouse_id: str | None`, `available: bool`, `error: str | None`. **Keywords**: `Field(ge=0)`, stub output schema

- [x] T022 Add module docstring and imports skeleton to `core/agent/tools.py`:
  ```python
  """
  Why this exists: LangGraph async tool registry for the sales agent.
  What it does: Wraps Week 2 RAG pipeline as a typed @tool and provides
  inventory lookup stub. All I/O validated through Pydantic schemas (Article VI).
  DB session injected via factory closure pattern (see data-model.md §7).
  """
  from langchain_core.tools import tool
  from sqlalchemy.ext.asyncio import AsyncSession
  ```

- [x] T023 [P] Create `tests/contract/tools/conftest.py` for contract test shared fixtures. Add `pytest_plugins = ["pytest_asyncio"]`, a `@pytest.fixture` for `respx_mock` (using `respx.mock`), and `asyncio_mode = "auto"` marker. **Keywords**: `respx.mock`, `asyncio_mode`, pytest fixture scope

- [x] T024 [P] Create `tests/unit/test_agent_state.py` skeleton — add module docstring, imports (`pytest`, `core.agent.state`), and a placeholder `test_agent_state_imports` that just verifies `AgentState`, `IntentEnum`, `EscalationReasonEnum` are importable. Run `uv run pytest tests/unit/test_agent_state.py -v` to confirm green. **Keywords**: import smoke test

**Checkpoint**: All types defined and importable — run `uv run python -c "from core.agent.state import AgentState, IntentEnum; print('OK')"` before proceeding.

- [ ] T101 [P] Compile initial **Gold Dataset** (`tests/fixtures/gold_dataset.json`) — 10+ canonical QA pairs covering all 7 intent types, **in Vietnamese**. Used as the ground truth for Tier 2 manual evaluation (T100) and future automated grading (Article III). **Format**:
  ```json
  [
    {"id": "gd-001", "input": "Giá sản phẩm X là bao nhiêu?", "expected_intent": "PRICING", "expected_response_contains": ["giá", "VNĐ"], "tier": 2},
    {"id": "gd-002", "input": "Sản phẩm Y có sẵn hàng không?", "expected_intent": "AVAILABILITY", "expected_response_contains": ["có sẵn", "hết hàng"], "tier": 2},
    {"id": "gd-003", "input": "Xin chào", "expected_intent": "SMALLTALK", "expected_response_contains": ["xin chào", "chào mừng"], "tier": 2},
    ...
  ]
  ```
  **Required coverage**: 2× PRICING, 2× INFO_QUERY, 1× COMPARISON, 1× COMPLAINT (Vietnamese grievance phrase), 1× NEGOTIATION (discount request), 1× AVAILABILITY, 1× SMALLTALK, 1× edge-case (unknown product), **all in Vietnamese**. **Keywords**: gold dataset, Article III, Tier 2 eval, Vietnamese test data, `tests/fixtures/`, ground truth

---

## Phase 3: US5 — Contract Tests (TDD Red Phase)

**Goal**: Write contract tests BEFORE tool implementation so they fail first (TDD Red). This enforces FR-009: "Contract tests MUST exist before tool implementation is complete."

**Independent Test**: Run `uv run pytest tests/contract/ -v` — ALL tests should **FAIL** (not error) at this phase, proving the contracts are real tests.

- [ ] T025 [US5] Create `tests/contract/tools/baselines/rag_tool_baseline.json` — snapshot of `RAGSearchOutput` field names and types:
  ```json
  {
    "answer": "str",
    "declined": "bool",
    "citations": "list",
    "similarity_score": "float",
    "confidence_score": "float",
    "model_used": "str",
    "chunks_used": "int"
  }
  ```
  **Keywords**: schema drift detection, baseline snapshot, structural diff

- [ ] T026 [US5] Create `tests/contract/tools/baselines/inventory_tool_baseline.json` — snapshot of `InventoryLookupOutput` field names and types:
  ```json
  {
    "sku": "str",
    "stock_level": "int",
    "warehouse_id": "str|None",
    "available": "bool",
    "error": "str|None"
  }
  ```

- [ ] T027 [US5] Create `tests/contract/tools/test_rag_tool_contract.py` file with module docstring and imports. Add helper `load_baseline(name)` that reads from `baselines/` directory and `validate_schema_drift(model_class, baseline)` that compares field names. **Boilerplate**:
  ```python
  import json
  from pathlib import Path
  def load_baseline(name: str) -> dict:
      p = Path(__file__).parent / "baselines" / f"{name}.json"
      return json.loads(p.read_text())
  ```

- [ ] T028 [US5] Add Scenario 1 test to `tests/contract/tools/test_rag_tool_contract.py`: `test_rag_tool_valid_response` — mock `respx` to return 200 OK, call `make_rag_tool(db)`, assert output is `RAGSearchOutput`, `declined=False`, `len(citations) >= 1`, `model_used` non-empty. Mark `@pytest.mark.asyncio`. **Keywords**: `respx.mock`, `@tool`, `RAGSearchOutput`, Pydantic parse

- [ ] T029 [US5] Add Scenario 2 test: `test_rag_tool_layer1_guard_declined` — mock vector search returning `similarity_score=0.40` (below 0.45 threshold), assert `declined=True`, `answer == DECLINE_MESSAGE`, verify LLM call count via `respx` = 0. **Keywords**: `CONFIDENCE_THRESHOLD=0.45`, Layer 1 guard, `respx.calls` count assertion

- [ ] T030 [US5] Add Scenario 3 test: `test_rag_tool_llm_429_graceful` — `respx` returns HTTP 429, assert tool does NOT raise exception, `declined=True`, response is string not None. **Keywords**: `respx.route().mock(side_effect=httpx.HTTPStatusError)`, LiteLLM retry

- [ ] T031 [US5] Add Scenario 4 test: `test_rag_tool_llm_500_graceful` — `respx` returns HTTP 500, assert graceful degradation, no unhandled exception, `declined=True`. **Keywords**: `httpx.HTTPStatusError`, `status_code=500`, graceful degradation

- [ ] T032 [US5] Add Scenario 5 test: `test_rag_tool_read_timeout` — `respx` raises `httpx.ConnectTimeout`, assert tool returns within 10s, `declined=True`. Use `asyncio.wait_for` or `pytest-asyncio` timeout. **Keywords**: `httpx.ConnectTimeout`, `respx.side_effect`, timeout guard

- [ ] T033 [US5] Add schema drift test to `test_rag_tool_contract.py`: `test_rag_tool_schema_no_drift` — loads baseline JSON, compares `RAGSearchOutput.model_fields.keys()` against baseline keys, fails if any field removed or renamed. **Boilerplate**:
  ```python
  def test_rag_tool_schema_no_drift():
      baseline = load_baseline("rag_tool_baseline")
      actual_fields = set(RAGSearchOutput.model_fields.keys())
      assert actual_fields == set(baseline.keys()), f"Schema drift: {actual_fields ^ set(baseline.keys())}"
  ```

- [ ] T034 [US5] Create `tests/contract/tools/test_inventory_tool_contract.py` with module docstring and imports. Add `load_baseline` helper (or import from shared utils). **Keywords**: `InventoryLookupInput`, `InventoryLookupOutput`, stub contract

- [ ] T035 [US5] Add Scenario 1 to `test_inventory_tool_contract.py`: `test_inventory_lookup_valid_sku` — call `inventory_lookup` with `sku="PROD-001"`, assert output matches `InventoryLookupOutput` schema, `available=True`, `error=None`. **Keywords**: `@tool`, stub returns mock data, schema validation

- [ ] T036 [US5] Add Scenario 2: `test_inventory_lookup_sku_not_found` — call with `sku="UNKNOWN-SKU"`, assert `available=False`, `stock_level=0`, `error` is non-None string. **Keywords**: 404-equivalent stub, graceful not-found

- [ ] T037 [US5] Add Scenario 3: `test_inventory_lookup_429_graceful` — inject HTTP 429 mock, assert `error` contains rate-limit message, no exception. **Keywords**: `respx`, 429 rate limit, graceful degradation

- [ ] T038 [US5] Add Scenario 4: `test_inventory_lookup_500_graceful` — HTTP 500 mock, assert `available=False`, `error` non-None. **Keywords**: server error, error field populated

- [ ] T039 [US5] Add Scenario 5: `test_inventory_lookup_timeout` — `httpx.ConnectTimeout` side-effect, assert returns within 5s, `error` contains "timeout", `available=False`. **Keywords**: timeout guard, `asyncio.wait_for`

- [ ] T040 [US5] Add schema drift test: `test_inventory_tool_schema_no_drift` — compare `InventoryLookupOutput.model_fields.keys()` against `inventory_tool_baseline.json`. **Keywords**: structural diff, schema regression guard

- [ ] T041 [US5] Run `uv run pytest tests/contract/ -v` and confirm ALL tests **fail** (not import-error). Fix any import errors. Expected: `FAILED` status (not `ERROR`). **Keywords**: TDD Red phase verification

---

## Phase 4: US1 — Intent-Classified Sales Response (Priority: P1) 🎯 MVP

**Goal**: Complete agent loop — classify intent, retrieve context, score confidence, generate cited answer.

**Independent Test**: `uv run python cli/run_agent.py "Giá sản phẩm X là bao nhiêu?"` — verify output includes a citation, confidence_score, and `model_used == "economy-chat"`.

- [ ] T042 [US1] Implement `make_rag_tool(db: AsyncSession)` factory in `core/agent/tools.py`. The inner `@tool async def rag_search(input: RAGSearchInput) -> RAGSearchOutput` must call `answer_with_rag(db, input.query, input.model)` and convert `RAGResult` → `RAGSearchOutput`. Implement `from_rag_result` classmethod. **Boilerplate**:
  ```python
  def make_rag_tool(db: AsyncSession):
      @tool
      async def rag_search(input: RAGSearchInput) -> RAGSearchOutput:
          result = await answer_with_rag(db, input.query, input.model)
          return RAGSearchOutput.from_rag_result(result)
      return rag_search
  ```

- [ ] T043 [US1] Implement `inventory_lookup` stub tool in `core/agent/tools.py`. Decorated with `@tool`. Always returns `InventoryLookupOutput(sku=input.sku, stock_level=99, warehouse_id=None, available=True, error=None)`. Add docstring: "Week 3 stub — real ERP integration deferred to Week 6." **Keywords**: `@tool`, stub implementation, Week 6 placeholder

- [ ] T044 [US1] Create `core/agent/nodes/router.py`. Implement `async def router_node(state: AgentState) -> Command` using `litellm.acompletion` with `model="economy-chat"`, `response_format=IntentClassification`. Use `classification.primary_intent` for routing; store both `primary_intent` and `secondary_intents` in state update. **Critical**: use `economy-chat` NOT `light-chat` (Ollama G1 constraint — research Decision 10). **Boilerplate**:
  ```python
  from langgraph.types import Command
  import litellm
  async def router_node(state: AgentState) -> Command:
      result = await litellm.acompletion(
          model="economy-chat",
          messages=[{"role": "user", "content": state["user_message"]}],
          response_format=IntentClassification,
      )
      classification = IntentClassification.model_validate_json(result.choices[0].message.content)
      next_node = _get_next_node(classification.primary_intent)
      return Command(
          goto=next_node,
          update={
              "intent": classification.primary_intent.value,
              "secondary_intents": [i.value for i in classification.secondary_intents],
              "intent_confidence": classification.confidence,
          },
      )
  ```

- [ ] T045 [US1] Implement `_get_next_node(primary_intent: IntentEnum) -> str` helper in `core/agent/nodes/router.py`. **Routing map**: `COMPLAINT/NEGOTIATION → "escalation_node"`, `SMALLTALK → "answer_node"` (SMALLTALK bypasses retrieval entirely—no RAG lookup), all others (`INFO_QUERY`, `PRICING`, `COMPARISON`, `AVAILABILITY`) → `"retrieval_node"`. Default unknown intent → `"retrieval_node"` (spec edge case). **Keywords**: routing map, `match/case`, SMALLTALK bypass, fallback default

- [ ] T046 [US1] Create `core/agent/nodes/retrieval.py`. Implement `async def retrieval_node(state: AgentState, tools: list) -> dict`. Find `rag_search` tool from `tools` list. Build `RAGSearchInput`. Call tool. Populate state: `retrieved_chunks`, `citations`, `similarity_score`, and propagate `declined=True` if `rag_result.declined` (Layer 1 fast-path). **Keywords**: tool invocation, Layer 1 propagation, `RAGSearchInput`, async tool call

- [ ] T047 [US1] Create `core/agent/nodes/confidence.py`. Implement `async def confidence_node(state: AgentState) -> dict`. Logic:
  1. If `state["declined"]` is True → return `{"declined": True, "confidence_score": state["similarity_score"]}` (fast-path from Layer 1).
  2. Compute fused score: if `rerank_score` is not None → `(1 - alpha) * similarity + alpha * rerank` else `confidence = similarity`.
  3. **Special case — INFO_QUERY borderline** (0.45 ≤ similarity < 0.7): Do NOT mark as declined; leave `declined=False` so T047b can route to escalation_node.
  4. For all other intents or INFO_QUERY with similarity ≥ 0.7: if `confidence_score < 0.70` → `declined=True`.
  5. Return state delta `{confidence_score, declined}`. **Keywords**: `AGENT_ALPHA=0.7`, Layer 2 guard, `AGENT_CONFIDENCE_THRESHOLD`, borderline INFO_QUERY carve-out, FR-007

- [ ] T047b [US1] Implement `_route_after_confidence(state: AgentState) -> str` routing helper in `core/agent/nodes/confidence.py`. Used as the conditional edge function from `confidence_node`. **Logic**: 
  - If `intent == "INFO_QUERY"` AND `0.45 ≤ similarity_score < 0.7` AND NOT already declined → `"escalation_node"` (borderline, FR-007 escalation).
  - Elif `declined=True` → `"answer_node"` (final decline).
  - Else → `"answer_node"` (normal acceptance). **Boilerplate**:
  ```python
  def _route_after_confidence(state: AgentState) -> str:
      # Prioritize INFO_QUERY borderline escalation before decline
      if (state["intent"] == "INFO_QUERY" and 
          not state["declined"] and 
          state["similarity_score"] < 0.7):
          return "escalation_node"  # borderline → try premium (FR-007)
      return "answer_node"  # declined or accepted
  ```
  **Keywords**: conditional edge, INFO_QUERY escalation path, FR-007 routing, priority ordering

- [ ] T048 [US1] Create `core/agent/nodes/answer.py`. Implement `async def answer_node(state: AgentState) -> dict`. All graph paths (both accepted AND declined) route through this node to ensure trace is always written (FR-008). If `state["declined"]` → return `{"response": DECLINE_MESSAGE, "model_used": None}` without LLM call. Else: call `litellm.acompletion` with `model=state["model_used"] or "economy-chat"`, build prompt with citations context, return `{"response": content, "model_used": ...}`. Import `DECLINE_MESSAGE` from `services.rag.constants`. **Keywords**: `DECLINE_MESSAGE`, universal trace path, citation context injection, LiteLLM call

- [ ] T048b [P] Add **pseudo-code + logic flowchart** comment block to `core/agent/nodes/confidence.py` to clarify the two-layer guard + escalation interaction. Document the three paths:
  ```python
  # CONFIDENCE NODE LOGIC (FR-007 + FR-010 interaction)
  # Path 1: Declined at Layer 1 (sim < 0.45) → skip confidence check, return declined=True
  # Path 2: INFO_QUERY borderline (0.45 ≤ sim < 0.7) → don't decline yet, route to escalation_node
  # Path 3: Normal (0.45 ≤ sim < 0.7 non-INFO or sim ≥ 0.7) → compute fused score
  #         If fused < 0.70 → declined=True (Layer 2 guard)
  #         Else → declined=False (proceed to answer_node)
  ```
  Reference data-model.md §5 Graph Topology in the comment. **Keywords**: pseudo-code, logic clarification, Article IV integration-first

- [ ] T049 [US1] Implement `_write_model_trace` call at end of `answer_node` in `core/agent/nodes/answer.py`. Write to `agent_v1.model_traces` regardless of whether the run was accepted or declined — this is the **universal trace point** (FR-008 requires trace after every run). The `metadata_` JSONB MUST include: `escalation_reason`, `escalation_failure` (bool — FR-007 edge case key), `guard_decision` (`"ACCEPTED"` or `"REJECTED"`), `declined` flag, and `intended_model`. On trace write failure: log to `sys.stderr` with context (e.g., `f"[TRACE_FAIL] session={state['session_id']}, error={e}"`) and continue without blocking response. **Boilerplate**:
  ```python
  try:
      _write_model_trace(state, metadata_)
  except Exception as e:
      import sys
      print(f"[TRACE_FAIL] session_id={state.get('session_id')}, error={e}", file=sys.stderr)
  ```
  **Keywords**: `model_traces`, JSONB `metadata_`, `escalation_failure` key, `intended_model`, universal trace, FR-008, stderr logging, fail-safe

- [ ] T050 [US1] Create `core/agent/graph.py` with `build_graph()` function. Create `StateGraph(AgentState)`. Add nodes: `router_node`, `retrieval_node`, `confidence_node`, `escalation_node`, `answer_node` (5 nodes total). Add edges: START → router_node (via `Command` routing), retrieval_node → confidence_node, **conditional** confidence_node → (escalation_node OR answer_node via `_route_after_confidence`), escalation_node → answer_node, answer_node → END. Set `recursion_limit=settings.AGENT_MAX_TURNS`. **Boilerplate**:
  ```python
  from langgraph.graph import StateGraph, START, END
  def build_graph(checkpointer=None):
      builder = StateGraph(AgentState)
      builder.add_node("router_node", router_node)
      ...
      return builder.compile(checkpointer=checkpointer)
  ```

- [ ] T051 [US1] Add **conditional edge** from `confidence_node` in `core/agent/graph.py` using `_route_after_confidence` helper. Three possible destinations: `"answer_node"` (declined OR normal), `"escalation_node"` (INFO_QUERY borderline). **Boilerplate**:
  ```python
  from core.agent.nodes.confidence import _route_after_confidence
  builder.add_conditional_edges("confidence_node", _route_after_confidence)
  ```
  **Rationale**: INFO_QUERY with `0.45 ≤ similarity < 0.7` must reach `escalation_node` to select premium model (FR-007); this path was impossible with an unconditional edge to `answer_node`.

- [ ] T052 [US1] Add `get_mermaid_diagram()` function to `core/agent/graph.py` that calls `build_graph().get_graph().draw_mermaid()` and returns the diagram string. Also add `export_mermaid_to_file(path: str)` convenience function. **Keywords**: `draw_mermaid()`, FR-004, static artifact, `docs/week3/agent-graph.mmd`

- [ ] T053 [US1] Create `cli/run_agent.py` debug CLI script. Accept positional `message` arg, optional `--stream` flag, optional `--session` arg. Build graph with `MemorySaver`. Call `graph.ainvoke` with initial state. Print final `state["response"]`. **Boilerplate**:
  ```python
  #!/usr/bin/env python
  """Debug CLI for LangGraph agent — Article I exemption (no parser, offline use only)."""
  import asyncio, sys
  from langgraph.checkpoint.memory import MemorySaver
  from core.agent.graph import build_graph
  async def main(message: str, stream: bool = False, session: str = "debug-session"):
      graph = build_graph(checkpointer=MemorySaver())
      config = {"configurable": {"thread_id": session}}
      ...
  if __name__ == "__main__":
      asyncio.run(main(sys.argv[1]))
  ```

- [ ] T054 [US1] Implement `AgentState` initialization helper `make_initial_state(user_message: str, session_id: str) -> AgentState` in `core/agent/state.py`. Sets all required fields with safe defaults — all boolean flags MUST be explicitly `False` (not falsy or None). **Boilerplate**:
  ```python
  def make_initial_state(user_message: str, session_id: str) -> AgentState:
      return AgentState(
          session_id=session_id,
          user_message=user_message,
          messages=[],
          intent=None,
          secondary_intents=[],
          intent_confidence=0.0,
          retrieved_chunks=[],
          citations=[],
          similarity_score=0.0,
          rerank_score=None,
          confidence_score=0.0,
          model_used=None,
          escalation_flag=False,       # explicit False (FR-007)
          escalation_failure=False,    # explicit False (FR-007)
          escalation_reason=None,
          response=None,
          declined=False,              # explicit False (SC-001)
          error=None,
      )
  ```
  **Keywords**: state initialization, TypedDict factory, explicit bool defaults, safe defaults

- [ ] T055 [US1] Write unit test `tests/unit/test_agent_state.py::test_agent_state_fields_complete` — verify `AgentState` TypedDict has all required fields from spec FR-001: `session_id`, `intent`, `model_used`, `escalation_flag`, `escalation_failure`, `response`, `similarity_score`, `rerank_score`, `confidence_score`, `intent_confidence`, `citations`, `messages` (do NOT check for `conversation_history` — it is NOT a separate field, `messages` is the canonical name per FR-001). Assert all required field names exist in `AgentState.__annotations__`. **Keywords**: TypedDict `__annotations__`, FR-001 compliance, `messages` not `conversation_history`

- [ ] T056 [US1] Write unit test `tests/unit/test_agent_state.py::test_intent_enum_values` — assert all 7 `IntentEnum` values exist, confirm `str(IntentEnum.INFO_QUERY)` serializes correctly. **Keywords**: `str(Enum)`, enum serialization, JSONB compatibility

- [ ] T057 [US1] Write unit test `tests/unit/test_router_node.py` — mock `litellm.acompletion` to return `IntentClassification(intent=INFO_QUERY, confidence=0.9, reasoning="test")`. Call `router_node(state)`. Assert `Command.goto == "retrieval_node"`, state update has `intent="INFO_QUERY"`. Use `unittest.mock.AsyncMock`. **Keywords**: `AsyncMock`, `litellm.acompletion` mock, `Command.goto` assertion

- [ ] T058 [US1] Write unit test `tests/unit/test_confidence_node.py::test_fused_score_with_reranker` — call `confidence_node` with `similarity_score=0.8`, `rerank_score=0.9`, `declined=False`. Assert `confidence_score ≈ (1-0.7)*0.8 + 0.7*0.9 = 0.87`. **Keywords**: fusion formula, `pytest.approx`, AGENT_ALPHA=0.7

- [ ] T059 [US1] Write unit test `tests/unit/test_confidence_node.py::test_fused_score_no_reranker` — call with `similarity_score=0.75`, `rerank_score=None`. Assert `confidence_score == 0.75` (α=0 fallback). **Keywords**: α=0 fallback, dev mode, no reranker

- [ ] T060 [US1] Write unit test `tests/unit/test_confidence_node.py::test_layer1_fast_path` — call `confidence_node` with `declined=True` (Layer 1 already fired), assert function returns immediately with `declined=True` and skips fusion computation (verify by checking `confidence_score == state["similarity_score"]`). **Keywords**: dual-layer guard, Layer 1 propagation, fast-path

- [ ] T061 [US1] Write integration test `tests/integration/test_agent_flow.py::test_info_query_full_flow` — build graph with `MemorySaver`, mock `litellm.acompletion` for router (INFO_QUERY) and answer nodes, mock DB session with fake `answer_with_rag` result (`similarity_score=0.85`, `declined=False`). Assert final state has `intent="INFO_QUERY"`, `model_used="economy-chat"`, `escalation_flag=False`, `response` is non-empty. **Keywords**: `MemorySaver`, `ainvoke`, integration test, mocked LLM

- [ ] T062 [US1] Run `uv run pytest tests/unit/ tests/contract/ -v --tb=short` and confirm all unit tests pass and contract tests still fail (Red stays Red until tools implemented). **Keywords**: TDD verification, unit pass / contract fail

---

## Phase 5: US2 — Complaint/Negotiation Escalation (Priority: P2)

**Goal**: Implement intent-first escalation so COMPLAINT/NEGOTIATION always uses premium model.

**Independent Test**: `uv run python cli/run_agent.py "Tôi muốn khiếu nại về đơn hàng"` — verify `model_used` is the premium model alias and `escalation_flag=True`.

- [ ] T063 [US2] Create `core/agent/nodes/escalation.py`. Implement `async def escalation_node(state: AgentState) -> dict` as pure Python (zero LLM call). **Logic**: 
  - **Intent-based** (COMPLAINT or NEGOTIATION): Check `state["intent"]` AND `state["secondary_intents"]` — if ANY intent is COMPLAINT/NEGOTIATION → `escalate=True`, `reason="intent_escalation"`, `selected_model=settings.PREMIUM_MODEL` (alias, not hardcoded).
  - **Score-based** (INFO_QUERY borderline): If `state["intent"] == "INFO_QUERY"` (from router) → `escalate=True`, `reason="low_confidence"`, `selected_model=settings.PREMIUM_MODEL`.
  - Otherwise → `escalate=False`, `reason="none"`, selected_model stays `None`.
  - Return state delta: `{escalation_flag, escalation_reason, model_used}`. **Keywords**: intent-first escalation, multi-intent check, INFO_QUERY score-based, pure Python, `EscalationDecision`, zero LLM cost

- [ ] T064 [US2] Add premium model fallback logic in `escalation_node`: if `settings.PREMIUM_MODEL` is unavailable (check via LiteLLM router health or config), fall back to `economy-chat` and set `escalation_failure=True` in state. Log warning. **Boilerplate**: wrap escalation model check in `try/except`, log to `logfire.warning("escalation_failure", reason=str(e))`. **Keywords**: graceful fallback, `escalation_failure`, `logfire.warning`

- [ ] T065 [US2] Wire `escalation_node` into `core/agent/graph.py`. Add to `StateGraph`. Ensure edge: `router_node` → `escalation_node` (via `Command` for COMPLAINT/NEGOTIATION), `escalation_node` → `answer_node`. **Keywords**: `Command(goto="escalation_node")`, graph edges, node registration

- [ ] T066 [US2] Update `answer_node` in `core/agent/nodes/answer.py` to write full escalation context in `model_trace.metadata_` JSONB. Ensure `metadata_` includes: `escalation_reason` (e.g. `"intent_escalation"`), `escalation_failure` (bool — `True` if premium was unavailable and economy was used as fallback, FR-007 edge case), and `escalation_flag` (bool). **Keywords**: `metadata_` JSONB, `model_traces`, `escalation_reason`, `escalation_failure` key, FR-007

- [ ] T067 [US2] Write unit test `tests/unit/test_escalation_node.py::test_complaint_escalates_to_premium` — call `escalation_node` with `state["intent"]="COMPLAINT"`. Assert `result["escalation_flag"]=True`, `result["escalation_reason"]="intent_escalation"`, `result["model_used"]` contains premium model alias. **Keywords**: `AsyncMock`, intent-first logic, premium model assertion

- [ ] T068 [US2] Write unit test `tests/unit/test_escalation_node.py::test_negotiation_escalates_to_premium` — same as T067 but with `intent="NEGOTIATION"`. Assert `escalate=True` and `reason="intent_escalation"` (not `"low_confidence"`). **Keywords**: SC-002 compliance, NEGOTIATION path

- [ ] T069 [US2] Write unit test `tests/unit/test_escalation_node.py::test_info_query_no_escalation` — call `escalation_node` with `intent="INFO_QUERY"`. Assert `escalation_flag=False`, `reason="none"`, `model_used="economy-chat"`. **Keywords**: no-escalation path, economy model retained

- [ ] T070 [US2] Write unit test `tests/unit/test_escalation_node.py::test_premium_model_unavailable_fallback` — mock premium model as unavailable. Assert fallback to economy model and state contains `escalation_failure` flag in trace metadata. **Keywords**: graceful fallback, `escalation_failure`, model unavailability

- [ ] T071 [US2] Write integration test `tests/integration/test_agent_flow.py::test_complaint_escalation_flow` — build graph with `MemorySaver`, mock router returning `COMPLAINT`, verify final state has `model_used=premium_model`, `escalation_flag=True`, `escalation_reason="intent_escalation"`. **Keywords**: full flow, escalation integration, SC-002

- [ ] T071b [US2] Write unit test `tests/unit/test_escalation_node.py::test_simple_pricing_no_escalation` — ensure Article XII compliance (prevent wasteful escalation). Call `escalation_node` with `intent="PRICING"` and high similarity_score=0.95. Assert `escalation_flag=False`, `model_used=None` (no premium), `reason="none"`. **Rationale**: PRICING queries with high confidence should use economy model; only COMPLAINT/NEGOTIATION force premium (Article XII — cost-aware routing). **Keywords**: negative escalation test, Article XII, no-escalation path, cost efficiency

- [ ] T072 [US2] Run `uv run pytest tests/unit/test_escalation_node.py tests/integration/test_agent_flow.py -v` — all must pass. **Keywords**: US2 verification checkpoint

---

## Phase 6: US3 — Low-Confidence Fallback Guard (Priority: P2)

**Goal**: Agent returns safe fallback under 200ms when confidence < 0.7 without calling LLM.

**Independent Test**: Submit query with no relevant data in DB — verify `response == DECLINE_MESSAGE`, `model_used=None`, `escalation_flag=False`, and response arrives in < 200ms.

- [ ] T073 [US3] Add `test_confidence_node_layer2_guard_fires` to `tests/unit/test_confidence_node.py` — set `similarity_score=0.55`, `rerank_score=None`, `declined=False`. Assert confidence_node sets `declined=True`, `confidence_score=0.55` (below 0.70 threshold). This test should already pass from T047 implementation. **Keywords**: Layer 2 guard, threshold 0.70, SC-003

- [ ] T074 [US3] Add `test_confidence_node_accepted` to `tests/unit/test_confidence_node.py` — set `similarity_score=0.85`, `rerank_score=None`, `declined=False`. Assert `declined=False`, `confidence_score=0.85`. **Keywords**: accepted path, threshold pass

- [ ] T075 [US3] Add `test_answer_node_fallback_no_llm_call` to `tests/unit/` — mock `litellm.acompletion` with `AsyncMock`. Call `answer_node` with `declined=True`. Assert LLM was NOT called (`mock.assert_not_called()`), `response == DECLINE_MESSAGE`. **Note on `model_used`**: assert `state["model_used"] is None` (no model was invoked in this run) BUT verify `_write_model_trace` is called with `metadata_` containing `"intended_model"` key (FR-008: audit log must not hide escalation decisions even for declined runs). **Keywords**: SC-003 < 200ms (no LLM), `assert_not_called`, DECLINE_MESSAGE, `intended_model` in trace metadata

- [ ] T076 [US3] Add `test_answer_node_fallback_state` to `tests/unit/` — verify `answer_node` with `declined=True` returns state update with `escalation_flag=False` (not changed to True). This guards against a bug where declined state could be confused with escalation. **Keywords**: state field isolation, declined vs escalation_flag

- [ ] T077 [US3] Write integration test `tests/integration/test_agent_flow.py::test_low_confidence_fallback` — mock `answer_with_rag` returning `similarity_score=0.50`, `declined=False` (passes Layer 1). Verify agent: passes retrieval → confidence_node sets `declined=True` (0.50 < 0.70) → routes to END without calling answer LLM. Assert `response == DECLINE_MESSAGE`, `model_used is None`. **Keywords**: dual-layer guard integration, Layer 1 pass + Layer 2 fail, expected behavior

- [ ] T078 [US3] Write integration test `tests/integration/test_agent_flow.py::test_layer1_declined_propagation` — mock `answer_with_rag` returning `declined=True` (Layer 1 fires, `similarity_score=0.40`). Verify confidence_node fast-path: `declined=True` propagated immediately, no fusion computed. Assert `response == DECLINE_MESSAGE`. **Keywords**: Layer 1 propagation, fast-path, data-model.md §4

- [ ] T079 [US3] Add performance assertion to `test_low_confidence_fallback`: measure wall time of graph execution, assert `< 0.2s` (SC-003). Use `time.perf_counter()` or `pytest-benchmark`. **Keywords**: `time.perf_counter`, SC-003, 200ms budget, no-LLM path

---

## Phase 7: US4 — Per-Node Streaming (Priority: P3)

**Goal**: Developer can see step-by-step node output as graph executes.

**Independent Test**: `uv run python cli/run_agent.py --stream "Còn hàng không?"` — verify ≥ 4 distinct event types emitted (router, retrieval/escalation, confidence, answer) before final response.

- [ ] T080 [US4] Define `NodeStreamEvent` Pydantic model in `core/agent/state.py`: `node_name: str`, `state_snapshot: dict`, `timestamp: str` (ISO format). This is the structured event format per FR-006. **Boilerplate**:
  ```python
  from datetime import datetime, timezone
  class NodeStreamEvent(BaseModel):
      node_name: str
      state_snapshot: dict
      timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
  ```

- [ ] T081 [US4] Add `astream_agent(message: str, session_id: str, db, checkpointer) -> AsyncGenerator[NodeStreamEvent, None]` function to `core/agent/graph.py`. Uses `graph.astream_events(input, config, version="v2")`. Filter for `"on_chain_end"` events. Extract **delta** from `event["data"]["output"]` (not the raw `event["data"]` which may contain the full accumulated state) — per FR-006, `state_snapshot` MUST be a delta (only fields changed by that node). **Boilerplate**:
  ```python
  async def astream_agent(message, session_id, db, checkpointer):
      graph = build_graph(checkpointer=checkpointer)
      config = {"configurable": {"thread_id": session_id}}
      async for event in graph.astream_events(initial_state, config, version="v2"):
          if event["event"] == "on_chain_end" and event["name"] in GRAPH_NODES:
              # event["data"]["output"] is the dict returned by the node (delta only)
              delta = event["data"].get("output") or {}
              yield NodeStreamEvent(node_name=event["name"], state_snapshot=delta)
  ```
  **Keywords**: `astream_events`, `version="v2"`, `on_chain_end`, delta extraction, `event["data"]["output"]`, FR-006

- [ ] T082 [US4] Update `cli/run_agent.py` to handle `--stream` flag. When `--stream` is set, call `astream_agent()` and print each `NodeStreamEvent` as: `[{node_name}] {key summary from state_snapshot}`. **Boilerplate**:
  ```python
  if stream:
      async for event in astream_agent(message, session, db, MemorySaver()):
          print(f"[{event.node_name}] {json.dumps(event.state_snapshot, indent=None)[:120]}")
  ```
  **Keywords**: streaming CLI output, `AsyncGenerator`, `--stream` argparse flag

- [ ] T083 [US4] Write integration test `tests/integration/test_agent_flow.py::test_streaming_emits_events` — run `astream_agent` with mock LLM, collect all `NodeStreamEvent` instances into a list. Assert `len(events) >= 4`. Assert at minimum one event each for: `router_node`, and one of `retrieval_node`/`escalation_node`, `answer_node`. **Keywords**: streaming event collection, SC-004, execution replay

- [ ] T084 [US4] Write integration test `tests/integration/test_agent_flow.py::test_streaming_events_have_required_fields` — verify each `NodeStreamEvent` has non-empty `node_name`, non-null `state_snapshot`, and valid ISO `timestamp`. **Keywords**: `NodeStreamEvent`, FR-006 validation, Pydantic parse

- [ ] T085 [US4] Write integration test `tests/integration/test_agent_flow.py::test_streaming_execution_replay` — verify that collected events, when sorted by timestamp, reconstruct the agent execution path in the correct order (router → retrieval/escalation → confidence → answer). **Keywords**: SC-004, execution replay from events

---

## Phase 8: US5 — Contract Test Completion & Polish

**Goal**: Make contract tests GREEN, add schema drift detection, write ADR, export Mermaid, final lint.

**Independent Test**: `uv run pytest tests/contract/ -v` — ALL 11 contract tests **PASS**.

- [ ] T086 [P] [US5] Make `test_rag_tool_valid_response` pass (T028) — verify `make_rag_tool(db)` correctly bridges `RAGResult` → `RAGSearchOutput` via `from_rag_result`. Fix any field mapping issues. Run single test to confirm green. **Keywords**: TDD Green phase, `from_rag_result`, field mapping

- [ ] T087 [P] [US5] Make `test_rag_tool_layer1_guard_declined` pass (T029) — verify `declined=True` propagation from `answer_with_rag` result, LLM call count = 0 assertion. **Keywords**: Layer 1 propagation, `respx.calls`, call count zero

- [ ] T088 [P] [US5] Make error/timeout scenarios green (T030-T032) — verify `RAGSearchOutput` is always returned (never raises). Check LiteLLM error handling in `make_rag_tool`. **Keywords**: graceful degradation, exception wrapping, `try/except`

- [ ] T089 [P] [US5] Make inventory contract tests green (T035-T039) — stub `inventory_lookup` always returns mock data, but tests mock the external call. Verify stub returns valid `InventoryLookupOutput` schema. **Keywords**: stub contract, schema validation, Week 3 mock data

- [ ] T090 [US5] Run `uv run pytest tests/contract/ -v` — confirm ALL 11 contract tests PASS. Screenshot or save output. **Keywords**: TDD Green phase complete, contract test validation

- [ ] T091 [P] Create `docs/adr/002-langgraph-orchestration.md` — document: Context (Week 2 linear pipeline → Week 3 branching needed), Decision (LangGraph, Article II exemption), Consequences (50ms compile, Week 4 interrupt-ready), Alternatives (manual while loop rejected). **Keywords**: ADR, LangGraph rationale, Article II, `interrupt_before`

- [ ] T092 [P] Run `uv run python -c "from core.agent.graph import export_mermaid_to_file; export_mermaid_to_file('docs/week3/agent-graph.mmd')"` to generate Mermaid diagram. Verify file exists and contains all 5 node names. **Keywords**: `draw_mermaid()`, FR-004, artifact generation

- [ ] T093 [P] Run ruff linter on all new agent files: `uv run ruff check core/agent/ cli/run_agent.py tests/contract/ tests/unit/ --select ALL --ignore ANN,D`. Fix any violations (no blocking I/O, no global state, no direct SDK imports). **Keywords**: `ruff check`, ASYNC rules, Article V compliance

- [ ] T094 [P] Constitution validation — run commands from `quickstart.md`:
  1. `grep -rn "re\.\(match\|search\|findall\)" core/agent/ && echo VIOLATION || echo CLEAN`
  2. `grep -rn "^import openai\|^from openai\|^import anthropic" core/agent/ && echo VIOLATION || echo CLEAN`
  3. `uv run ruff check core/agent/ --select ASYNC`
  All must report CLEAN. **Keywords**: SC-007, no regex, no direct SDK, Article V

- [ ] T095 Run full test suite: `uv run pytest tests/ -v --tb=short -q`. Note: integration tests require DB + Ollama. For CI without Ollama: `uv run pytest tests/unit/ tests/contract/ -v`. All unit + contract must pass. **Keywords**: full suite, regression check, CI baseline

- [ ] T096 [P] Add `pytest.ini` or `pyproject.toml` `[tool.pytest.ini_options]` section for `asyncio_mode = "auto"` if not already set. Run `uv run pytest --co -q tests/contract/` to confirm test collection works without warnings. **Keywords**: `asyncio_mode = "auto"`, `pytest-asyncio`, no `@pytest.mark.asyncio` boilerplate needed

- [ ] T097 [P] Create `tests/integration/test_agent_flow.py` module docstring and add `pytestmark = pytest.mark.integration` to mark all tests as integration tier. Add to `conftest.py` at root: `--ignore-glob=tests/integration` for fast CI mode. **Keywords**: pytest markers, `pytest.mark.integration`, CI tiers

- [ ] T098 [P] Verify `cli/run_agent.py` is executable: `chmod +x cli/run_agent.py`. Run help: `uv run python cli/run_agent.py --help`. Confirm `--stream` and `--session` flags shown. **Keywords**: CLI usability, Article I, debug script

- [ ] T099 Final smoke test: Run `uv run python cli/run_agent.py "Giá sản phẩm X là bao nhiêu?"` (requires Ollama + DB). If offline only: run `uv run pytest tests/unit/ tests/contract/ -v` and confirm all 100% pass. **Keywords**: end-to-end smoke, SC-001 verification

- [ ] T102 [P] Export Mermaid diagram to `docs/week3/agent-graph.mmd` as a committed static artifact (FR-004). Call `export_mermaid_to_file("docs/week3/agent-graph.mmd")` from `core/agent/graph.py`. Run once and commit the output. **Boilerplate**:
  ```bash
  mkdir -p docs/week3
  uv run python -c "from core.agent.graph import export_mermaid_to_file; import asyncio; export_mermaid_to_file('docs/week3/agent-graph.mmd')"
  git add docs/week3/agent-graph.mmd
  ```
  **Keywords**: `draw_mermaid()`, FR-004, `docs/week3/agent-graph.mmd`, static artifact, committed diagram

- [ ] T100 [US2] Tier 2 manual evaluation — empathetic tone check for sensitive intents (Article III). Run the agent with 3 test inputs covering `COMPLAINT` and `NEGOTIATION` intents. For each response, verify: (1) tone is empathetic (no dismissive language), (2) response does NOT directly commit to pricing/refunds without HITL (Week 4 scope), (3) escalation trace shows `escalation_flag=True`. Record results in `docs/week3/tier2-eval.md`. **Acceptance criteria**: all 3 responses pass human review. **Keywords**: Tier 2 eval, US2 acceptance criteria, Article III, empathetic tone, manual QA, `docs/week3/tier2-eval.md`

---

## Dependencies (Story Completion Order)

```
Phase 1 (Setup)
    └─→ Phase 2 (Foundational: state types + tool schemas)
            └─→ Phase 3 (US5: Contract Tests — TDD Red, must be FAILING)
                    └─→ Phase 4 (US1: Core agent loop — makes some contracts GREEN)
                            ├─→ Phase 5 (US2: Escalation — depends on graph.py wired)
                            ├─→ Phase 6 (US3: Fallback — depends on confidence_node complete)
                            └─→ Phase 7 (US4: Streaming — depends on graph.py astream)
                                    └─→ Phase 8 (Polish: all contracts GREEN, lint, ADR)
```

**Parallelizable within phases**:
- Phase 2: T012-T013 (enums) ∥ T018-T021 (tool schemas)
- Phase 3: T028-T032 (RAG contract tests) ∥ T034-T039 (Inventory contract tests)
- Phase 4: T044 (router) ∥ T046 (retrieval) ∥ T047 (confidence) ∥ T048 (answer) — all different files
- Phase 5: T067-T070 (escalation unit tests) — all parallel
- Phase 8: T091 (ADR) ∥ T092 (Mermaid) ∥ T093-T094 (lint/constitution)

---

## Implementation Strategy

### MVP Scope (Phase 1–4 only)

Completing Phases 1–4 delivers a **working agent** that:
- Classifies intent with economy model
- Routes to RAG retrieval
- Applies dual-layer confidence guard
- Returns cited answer or safe fallback
- Passes all unit + contract tests (TDD Green for US1)

This is a shippable increment. Phases 5–8 add escalation, fallback guard hardening, streaming, and polish.

### Suggested Execution Order

1. **Developer 1**: T001-T024 (Setup + Foundational, sequential)
2. **After T024**: Branch — Developer 1 continues T025-T041 (Contract Tests); Developer 2 starts T042-T053 (US1 implementation)
3. **After T062**: Phases 5, 6, 7 can be executed in parallel by different developers
4. **All merge to Phase 8**: lint, ADR, Mermaid, final test run

---

## Task Count Summary

| Phase | Story | Tasks | Parallelizable |
|-------|-------|-------|---------------|
| Phase 1: Setup | — | T001–T010 (10) | 7 |
| Phase 2: Foundational | — | |  |
| Phase 3: US5 (Contract Red) | US5 |  |  |
| Phase 4: US1 (Core Loop) | US1 |  |  |
| Phase 5: US2 (Escalation) | US2 | |  |
| Phase 6: US3 (Fallback) | US3 |  |  |
| Phase 7: US4 (Streaming) | US4 |  |  |
| Phase 8: Polish + US5 Green | US5 | T087–T106 (20) | 9 |
| **Total** | | **106 tasks** | **48** |
