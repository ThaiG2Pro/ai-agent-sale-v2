# Impact Analysis — 3 cost-cut candidates on the 80%-common-case path (T11)

Codebase state: main @ 7c5517e. Intended destination: `wayfinder/research/cost-cut-impact-analysis.md`.
Context: T08 hybrid architecture (enum router + fast-path = 80% path) and the T03 combo
(history-aware router reads last-3-turns `messages` + `previous_intent` from checkpointed state;
`make_initial_state` stops wiping `intent`/`secondary_intents`). Cuts below must not starve that combo.

---

## Candidate 1 — Keyword fast-path for SMALLTALK (skip router LLM + answer LLM, reply from template)

### What a normal SMALLTALK turn does today
- `core/agent/nodes/router.py`: 1 LLM call (`economy-chat`, structured `IntentClassification`) →
  `Command(goto="answer_node", update={intent: "SMALLTALK", secondary_intents, intent_confidence})`.
  Precedent already exists: cancel keywords short-circuit the router with 0 LLM calls (lines 39–56).
- `core/agent/nodes/answer.py`: 1 LLM call on `light-chat` when `CHEAP_INTENT_LIGHT_ROUTING=True`
  (line 201), with the SC07 domain-guardrail system prompt (line 215). Returns
  `{"messages": [AIMessage(...)], "response": ...}` and writes a `model_traces` row (`_write_model_trace`).
- State/history writes that survive the turn (checkpointed via `add_messages` in
  `core/agent/state.py:172`): `HumanMessage` (from `make_initial_state`) + `AIMessage` (from
  answer_node), plus `intent="SMALLTALK"` — exactly the channels the T03 combo reads next turn.
- What SMALLTALK already does NOT do (so a cut loses nothing here):
  - `sales_intent_logs` / `intent_tracking`: skipped — SMALLTALK is in `SKIP_INTENT_EXTRACTION`
    (`core/agent/state.py:36`; enforced in `services/memory/background.py::_maybe_extract_intent`).
  - Episodic memory: skipped (`answer.py::_write_episodic_event` line 568 explicitly excludes SMALLTALK).
  - Memory retrieval: skipped (`memory_retrieval.py` line 60 early-returns on SMALLTALK).
  - Semantic cache: never written for SMALLTALK (no `canonical_query`/`query_vector` — retrieval never ran).
- What SMALLTALK DOES still feed: conversation summaries — `_maybe_summarize`
  (`services/memory/background.py`) counts and summarizes ALL `state["messages"]`, including
  SMALLTALK turns. Summaries → semantic memory embedding chain.

### Concrete risks
1. **Where the fast-path lives decides everything.** If implemented OUTSIDE the graph (reply
   before `graph.ainvoke` in `api/routes/agent.py` / `core/telegram/message_handler.py`), the turn
   never reaches the checkpointer: no `HumanMessage`/`AIMessage` appended, no `intent` update →
   the T03 history-aware router loses a turn of context and `previous_intent` goes stale; a
   greeting between two product turns silently vanishes from the last-3-turns window; summaries
   and `post_turn_tasks` also see a hole. **This variant starves T03 — do not build it.**
2. **Keyword misfire on mixed messages**: "chào shop, iPhone 15 giá bao nhiêu?" contains a greeting
   token but is PRICING. A substring match (like the current cancel-keyword check) would swallow
   the sales query. T08 also plans hesitation signals ("thôi", "để xem thêm") on the fast-path —
   "thôi" already overlaps the cancel keyword "thôi không mua"; ordering/precedence must be explicit.
3. **SC07 guardrail loss**: the LLM SMALLTALK prompt politely declines off-topic chitchat
   (cooking/weather/code). A pure template can only cover greeting patterns; off-topic messages
   that keyword-match nothing must still fall through to the LLM router.
4. Tests that encode current SMALLTALK behavior (update, don't fear):
   `tests/unit/test_router_node.py:89–116,195` (SMALLTALK → answer_node routing map),
   `tests/integration/test_agent_flow.py:281` (streaming test mocks router+answer LLM for "Xin chào!"),
   `tests/unit/test_memory_retrieval_node.py:63–83`, `test_intent_extractor.py:33`,
   `test_groundedness.py:236`, `test_background_tasks.py:414`. Eval gold set
   (`tests/eval/gold_dataset.json`) contains **zero** SMALLTALK cases — evals unaffected.
5. `model_traces` observability: the template path must still call `_write_model_trace` (or an
   equivalent) or the "80% self-handled" metric loses its SMALLTALK rows.

### Safe-cut conditions
- Implement INSIDE the graph: a keyword branch at the top of `router_node` returning
  `Command(goto="answer_node", update={intent: "SMALLTALK", intent_confidence: 1.0, ...})`
  (same shape as the cancel fast-path), plus a template branch in `answer_node` for SMALLTALK
  that returns `{"messages": [AIMessage(template)], "response": template}` without an LLM call.
  Checkpointed history, `intent` channel, summaries, and trace writes all stay intact → T03 combo fed.
- Keyword gate must be conservative: full-message anchored patterns / short messages only
  (≤ ~4 words), and NO product/price/order token present (reuse the token regexes already in
  `answer.py::_generate_catalog_response`). Anything mixed falls through to the LLM router.
- Cancel-keyword check keeps precedence over hesitation-signal keywords.
- Update the 6 test files above; template must satisfy the SC07 "greeting → introduce service" behavior.

**Verdict: SAFE-WITH-CONDITIONS** (in-graph implementation + conservative full-match gate).
Saves 2 LLM calls/turn on greeting turns; loses nothing the pipeline currently keeps.

---

## Candidate 2 — Semantic-cache check BEFORE the graph/router (0 LLM calls on hit)

### Where the cache sits today
- Implementation: `services/semantic_cache.py` — L1 SHA256 exact-hash + L2 pgvector cosine ≥ 0.95,
  both filtered by `model_name == settings.EMBED_MODEL` (embed model only — NOT chat-model or
  prompt version) and TTL (`CACHE_TTL_SECONDS`, default 3600; 0 = forever).
- Consulted inside `services/rag/pipeline.py::search_and_retrieve` (L1 at line 99 pre-normalize,
  L2 at line 196 post-embed) — i.e. **after the router**, and only on intents that route through
  `retrieval_node` (INFO_QUERY/PRICING/COMPARISON/AVAILABILITY/ORDER_PLACEMENT).
- On a hit, `answer_node` Path 1 returns the cached answer with a `CACHE_HIT` model_trace and
  still calls `_write_episodic_event`. Crucially, **HITL is not bypassed today**: routing keys on
  `intent`, so ORDER_PLACEMENT still goes confidence → `hitl_guard_node` even with `cached_answer` set.
- Written only in `answer_node::_write_cache` (accepted, grounded answers), keyed on the
  pronoun-EXPANDED `canonical_query`. Invalidation: `services/rag/ingest.py:295` full wipe on
  (re-)ingest only — **no invalidation hook on direct product price/stock updates outside ingest**;
  TTL is the only backstop (`state_freshness_validator_node` protects the order path, not answers).

### Concrete risks of moving the check pre-router
1. **Safety-path bypass (the killer):** with no intent classified, a COMPLAINT/NEGOTIATION/
   ORDER_PLACEMENT/CANCEL message that is ≥0.95-similar to a cached consult query gets a canned
   answer instead of escalation_node / hitl_guard_node / cancellation_node. L2 at 0.95 cannot tell
   "Mua iPhone 15 Pro" (ORDER) from "iPhone 15 Pro?" (INFO).
2. **HITL pause bypass:** `core/telegram/message_handler.py` calls `check_paused_session` (line 126)
   before invoking the graph; a message during an admin-review pause is queued, not answered.
   A cache check placed before that pause check would answer into a paused thread.
3. **T03 starvation:** answering outside the graph means no `messages`/`intent` checkpoint update —
   same hole as Candidate 1 risk #1. `previous_intent` and last-3-turns context skip a beat, and
   pronoun expansion (`retrieval.py::_expand_pronoun_query` uses prior citations) never runs, so
   "nó giá bao nhiêu?" raw text could L1-hit a WRONG product's cached entry (in-graph, the cache is
   keyed on the expanded query precisely to prevent this).
4. **Stale/personalized answers:** cached responses were generated WITH the original customer's
   `memory_context` baked into the prompt (`answer.py` memory_note) but the cache key has **no
   customer_id** — cross-customer replay of personalized content is already possible today and a
   pre-router cache widens the window (today it's narrowed by intent gating). Price/stock answers go
   stale for up to TTL after any change that doesn't pass through ingest.
5. Lost per-turn effects on hits outside the graph: `CACHE_HIT` model_trace, episodic-memory write,
   sales-intent extraction (intent unknown → `_maybe_extract_intent` can't run), budget/cap counters.
6. Marginal saving: an L1 hit **already costs zero LLM calls today** except the router
   classification (~1 small-model call). The pre-router move saves exactly that one router call.

### Safe-cut conditions (all required)
- Placement: after `check_paused_session`, and only as **L1 exact-hash** (raw short queries,
  no pronouns/ordinals — reuse `_PRONOUN_RE`/`_ORDINAL_RE` as a negative gate). No pre-router L2.
- Negative keyword gate for order/cancel/complaint/negotiation signals (the router's cancel
  keywords + order verbs) — any match falls through to the graph.
- Better alternative that keeps ~all the saving with none of the bypass risk: keep the cache check
  in-graph but let a **keyword-pre-classified intent** (Candidate-1 style whitelist:
  INFO_QUERY/PRICING/AVAILABILITY only) skip the router LLM, then hit L1/L2 in retrieval as today.
  HITL/escalation/T03/traces all preserved; router call saved on the same turns.
- Regardless of placement: add cache invalidation on price/stock mutation paths (or accept TTL=3600
  as the staleness ceiling and never raise it for price-bearing answers), and consider stamping
  answers-cache entries with a prompt/chat-model version alongside `EMBED_MODEL`.

**Verdict: DO-NOT-CUT as literally specified (pre-router/pre-graph).** The safe form is the
in-graph variant above (intent-whitelisted router skip + existing L1/L2), which is
safe-with-conditions and saves the same single router call.

---

## Candidate 3 — Conditional skip of memory_retrieval_node

### What the node does and feeds (`core/agent/nodes/memory_retrieval.py`)
- Cost profile: **0 LLM calls.** It does 1 embedding call (inside
  `SemanticMemoryService.retrieve`, local bge-m3/fastembed) + 1–2 pgvector queries
  (+ optional episodic recent-events query when the message has a time reference, WP-V2-4).
- Fills state channels downstream nodes expect: `memory_context`, `memory_retrieval_scores`,
  and conditionally `declined=False` (line 147 — memory RESCUES borderline turns).
- Already self-skips: SMALLTALK intent, missing customer_id, missing db → `_empty_update()`.
- Graph wiring (`core/agent/graph.py`): static edge `retrieval_node → memory_retrieval_node →
  confidence_node` (lines 237–240); router also targets it directly for FOLLOW_UP. So a
  "skip" cannot be an edge removal without a graph-schema change (`GRAPH_SCHEMA_VERSION` bump,
  checkpoint compat); the cheap form is an early-return inside the node (pattern already exists).

### What breaks if skipped for returning customers on INFO_QUERY
- `confidence_node` (`core/agent/nodes/confidence.py`):
  - line 85: `if state.get("memory_context"): is_declined = False` — without memory, a returning
    customer's borderline query about a previously-discussed product ("cái laptop em tư vấn đợt
    trước còn không?") gets declined or clarified instead of answered.
  - line 107: clarify gate includes `not state.get("memory_context")` — skipping memory makes
    clarify_node fire more often → MORE turns (clarify costs an extra round-trip), not fewer.
- `answer_node` (lines 236–247): loses the `memory_note` context block → generic, non-personalized
  answers; time-referenced queries ("hôm qua", "lần trước") lose episodic recall entirely and will
  typically produce "không tìm thấy" or a wrong-product answer.
- FOLLOW_UP intent routes THROUGH this node by design (router line 166) — must never be skipped there.
- Note `memory_retrieval_scores` is currently written but not read by any downstream node
  (only surfaced in stream events/tests) — no functional dependency.
- Tests: `tests/unit/test_memory_retrieval_node.py` (skip matrix), `tests/integration/` memory flows.

### Safe-cut conditions
- Only skip when the retrieval outcome cannot change: e.g. cache hit turns
  (`state.get("cached_answer")` set — answer_node ignores memory_note on Path 1 anyway) or
  high-confidence turns (`similarity_score ≥ AGENT_CONFIDENCE_THRESHOLD` AND no time-reference
  token AND intent not FOLLOW_UP). Implement as an early-return in the node, returning
  `_empty_update()`, keeping the graph topology and schema version untouched.
- Never skip on: FOLLOW_UP, borderline similarity (< 0.70), time-referenced messages, or when
  `awaiting_clarification` is pending.
- Honest framing for T10: this cut saves **one local embed + SQL round-trip**, no LLM tokens.
  On the zero-cost stack the win is latency-only and small; the downside (more declines/clarifies
  for returning customers) directly hurts the "80% self-handled" destination metric.

**Verdict: SAFE-WITH-CONDITIONS, but low value — recommend deprioritizing** (cut only the
cache-hit / high-confidence cases if at all; otherwise leave the node alone).

---

## Summary table

| # | Candidate | LLM calls saved (80% path) | Blast radius core | Verdict |
|---|-----------|---------------------------|-------------------|---------|
| 1 | Keyword fast-path SMALLTALK | 2 (router + answer) on greeting turns | router.py, answer.py, 6 test files; T03 needs in-graph impl | **safe-with-conditions** |
| 2 | Semantic cache pre-router | 1 (router) on cache-hit turns | HITL pause path, escalation/cancel routing, T03 history, pronoun cache keys, traces/episodic | **do-not-cut** as specified; in-graph intent-whitelisted variant is the safe form |
| 3 | Conditional skip memory_retrieval_node | 0 LLM (1 embed + SQL) | confidence rescue/clarify gate, answer personalization, FOLLOW_UP path | **safe-with-conditions, low value** |

Cross-cutting invariant for T10: any cut that answers a turn **outside the graph** breaks the T03
combo (checkpointed `messages` + `previous_intent`) and the HITL pause queue — every safe variant
above stays inside the graph and preserves the state writes.
