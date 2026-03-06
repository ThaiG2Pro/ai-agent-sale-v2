# Research: Human-in-the-Loop (HITL) Control System

**Phase 0 Output** | Branch: `004-human-in-loop-hitl` | Date: 2026-03-06  
**Source**: `docs/week4/techniques-reference.md` + existing codebase analysis

All NEEDS CLARIFICATION items resolved. No unknowns remain.

---

## Decision 1: Interrupt Mechanism — Dynamic `interrupt()` vs Static `interrupt_before`

**Decision**: Use **dynamic `interrupt()`** called inside node logic (not static `interrupt_before=[]` at compile time).

**Rationale**:
- `interrupt_before=["order_node"]` fires on EVERY invocation — even when confidence is high and cost is within budget. Wastes admin time.
- `interrupt()` is called conditionally: only when `confidence_score < 0.7` OR `estimated_token_cost > 8000`. Maximizes AI autonomy; escalates only when necessary (core SME philosophy).
- Enables cost guard to be placed in a dedicated `hitl_guard_node` AFTER retrieval and compression, with full context available for accurate token estimation.

**Implementation**: Place `interrupt(reason)` inside `hitl_guard_node`. The `interrupt()` call raises `GraphInterrupt`, pausing the thread at that point. Graph resumes via `graph.invoke(Command(resume=approval_payload), config=config)`.

**Alternatives Considered**:
- `interrupt_before=["order_node"]` — Rejected: always-on, ignores confidence/cost thresholds, doesn't match adaptive philosophy.
- External webhook trigger — Rejected: bypasses LangGraph's state machine, creates sync issues.

---

## Decision 2: State Persistence — Single Source of Truth

**Decision**: **`AsyncPostgresSaver`** (LangGraph built-in) is the ONLY state store. `HITLMetadata` table stores operational metadata ONLY (reason, timestamps, admin_id, status, escalation_count).

**Rationale**:
- Dual persistence (custom `InterruptedSession` table with full JSON state + PostgresSaver) creates sync bugs. If admin resumes graph, PostgresSaver is updated but custom table is not — LLM reads stale state.
- `AsyncPostgresSaver` creates 4 tables automatically: `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`. These are the canonical source of truth.
- `HITLMetadata` is lightweight operational data — not full state. Used for timeout logic, escalation tracking, and audit queries (not for resuming the graph).

**Security**: Use `JsonPlusSerializer(pickle_fallback=False)` to prevent RCE via pickle deserialization (CVE-2026-27794).

**Alternatives Considered**:
- Custom `InterruptedSession` table with full state JSON — Rejected: double persistence, sync bugs, redundant with LangGraph's checkpointer.
- In-memory state — Rejected: violates zero-restart persistence requirement.

---

## Decision 3: State Override Pattern — Pattern B (Atomic Update + Resume)

**Decision**: Use **Pattern B**: `graph.update_state(config, values, as_node="hitl_review_node")` followed by `graph.invoke(Command(resume=approval_payload), config=config)` as a single atomic operation.

**Rationale**:
- Pattern A (Fork from parent checkpoint) discards admin edits if the graph is re-run — dangerous for order correction.
- Pattern B stores the updated state as the new checkpoint BEFORE resuming. The resumed graph reads the corrected state. Audit trail preserved.
- `as_node="hitl_review_node"` identifies the update source (admin, not AI), ensuring correct edge routing in the graph after resume.

**Synthetic message injection**: Before calling `update_state`, insert `"SYSTEM [Admin override]: {field} updated from {old} to {new}"` into `messages` field. Position: immediately after customer's last message (not at tail). Prevents LLM from over-weighting older contradictory context.

**Alternatives Considered**:
- Pattern C (Time Travel) — Rejected: branching from historical checkpoint is for debugging, not production approval flows.
- Pattern A (Fork) — Rejected: doesn't preserve admin edits in the live conversation thread.

---

## Decision 4: Idempotency — Optimistic Locking + X-Idempotency-Key

**Decision**: Two-layer duplicate prevention:
1. **`X-Idempotency-Key`** header on `/review` POST (per `techniques-reference.md` §3). Admin UI sends unique key per approval action. If same key received twice, return `200 OK` with cached result (no duplicate processing).
2. **Optimistic locking**: `InterruptedSession.version` field (integer, starts at 0). Approval increments version. If incoming `expected_version` != current `version`, reject with `409 Conflict`.

**Rationale**: Double-click / lag scenarios are real in admin UIs. Both layers needed: idempotency key handles network retries; optimistic locking handles concurrent multi-admin scenarios.

**Alternatives Considered**:
- Redis-based distributed locking — Rejected: violates no-Redis lean constraint.
- Database row lock (`SELECT FOR UPDATE`) — Rejected: blocking, reduces throughput, PostgreSQL advisory locks are complex.

---

## Decision 5: Confidence Guard Threshold + Formula

**Decision**: Use **Confidence Fusion formula** from `techniques-reference.md` §8:

```
Confidence = (1 - α) × similarity_norm + α × rerank_norm   (α = 0.7)
```

Threshold: HITL triggers if `overall_confidence < 0.7`.

Min-Max normalization applied per session batch. RRF (§5) used for hybrid retrieval ranking before confidence calculation.

**Rationale**: Existing `AgentState` already has `similarity_score`, `rerank_score`, `confidence_score`. `hitl_guard_node` reads `confidence_score` directly — no new calculation needed if `confidence_node` is already upstream. If `rerank_score` is None (dev environment without reranker), fall back to `similarity_score` only.

**OOD Detection**: Missing confidence (None) treated as 0.0 — conservative escalation (FR spec, edge case).

---

## Decision 6: Cost Guard Threshold + Token Counting

**Decision**: Use `litellm.token_counter(model, messages)` on the compressed context window before calling the LLM. Threshold: **8000 tokens** per operation. Based on per-operation compressed context (not full conversation history).

**Cost estimation formula** (§6):
```python
estimated_input_tokens = litellm.token_counter(model=model_id, messages=compressed_messages)
# Core answer ≈ 42% of total response tokens (from reference)
estimated_total = estimated_input_tokens * 1.42
```

If `estimated_total > 8000` → `interrupt(reason="cost_limit_exceeded")`.

**Rationale**: Full conversation history would always exceed threshold for multi-turn sessions. Per-operation compressed context is the correct scope (confirmed in clarification Round 1, Q3).

---

## Decision 7: QueuedMessage Processing — Post-Approval Node

**Decision**: After `Command(resume=approval_payload)`, graph routes to `post_approval_node` (inserted between `hitl_guard_node` and `order_execution_node`). This node:
1. Queries `QueuedMessage` WHERE `session_id = X AND processed = false` ORDER BY `received_at ASC`
2. Merges each as `HumanMessage(content="Customer followed up: {text}")` appended to `state["messages"]`
3. Marks all as `processed = true` (batch update)
4. Returns updated state → proceeds to order execution

**Cleanup**: Nightly background job marks `archived = true` WHERE `processed = true AND received_at < NOW() - INTERVAL '90 days'`.

---

## Decision 8: Rejection Flow — `customer_support_node`

**Decision**: On admin rejection, graph routes to `customer_support_node` (not `__end__`). Node uses `LiteLLM` to compose empathetic message (cheap model — `economy` tier):

```
"We're unable to process your order because {rejection_reason}. 
Our support team will help you. Contact: {support_link}."
```

Then writes to `SupportQueue` table and transitions to `__end__`.

**Timeout path**: After 60 minutes with no admin review, `HITLService.check_timeouts()` (background task) writes to `SupportQueue` and sends Telegram notification via existing Telegram integration.

---

## Decision 9: HITL Node Integration in Graph

**Decision**: Insert `hitl_guard_node` between `confidence_node` and `answer_node` in the existing graph. New graph flow:

```
router_node → retrieval_node → confidence_node → hitl_guard_node
    ↓ (low confidence or high cost)            ↓ (approved / confidence OK)
[INTERRUPT - admin reviews]              post_approval_node → answer_node
    ↓ (approved)
Command(resume) → post_approval_node → answer_node

Rejection path: hitl_guard_node → customer_support_node → __end__
```

---

## Security Notes

- `JsonPlusSerializer(pickle_fallback=False)` — required (CVE-2026-27794)
- `SecretStr` for any API keys in Pydantic models (§9)
- `SensitiveLogFilter` on HITL endpoints to prevent admin_id/session_id leakage in logs
- Admin endpoints secured by existing `X-Admin-Key` dependency (already implemented in `api/dependencies.py`)
