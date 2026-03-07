# HITL Architecture Reconciliation & Fixes

**Date**: 2026-03-07 | **Status**: ✅ Reconciled

## Issues Identified & Fixed

### 1. ❌ Interrupt Mechanism Mismatch
**Issue**: Spec mandates `interrupt_before` (static), but plan/research use dynamic `interrupt()`
**Fix**: Adopted **dynamic `interrupt()` in guard node** (smarter, adaptive) as primary mechanism, with spec requirement for interrupting before sensitive operations satisfied through `hitl_guard_node` placement in graph topology.
**Rationale**: Dynamic interrupt is more cost-efficient (only escalates when necessary) and aligns with SME lean philosophy.

### 2. ❌ Missing Graph Edges
**Issue**: Tasks.md didn't wire `confidence_node → hitl_guard_node` or show threshold routing
**Fix**: 
- **T023**: Now explicitly adds conditional edge from `confidence_node` to `hitl_guard_node` when `confidence < 0.7`
- **T024**: Added `_route_after_confidence()` function for confidence-based routing
- Graph now routes: `confidence_node` → (OK path) → `answer_node` OR (guard path) → `hitl_guard_node`

### 3. ❌ Node Naming Inconsistency
**Issue**: Research used `hitl_checkpoint_node`, tasks used same, but `post_approval_node` vs `queue_consumer_node`
**Fix**: 
- Renamed `hitl_checkpoint_node` → `hitl_guard_node` (clearer intent: confidence + cost guard)
- Kept `queue_consumer_node` (aligns with spec)
- Added `state_freshness_validator_node` (new, for stale data checks)

### 4. ❌ Synthetic Message Format Violated Article VI
**Issue**: Tasks.md used plain string `"SYSTEM [Admin override]: ..."` instead of structured format
**Fix**: **T046** now requires structured dict:
```python
{
    "type": "admin_override",
    "field": "order_quantity",
    "old_value": 5,
    "new_value": 10,
    "admin_id": "admin-123",
    "timestamp": "2026-03-07T14:00:00Z",
    "reason": "..."
}
```
**Article VI Compliance**: ✅ Restored structured boundaries for auditability.

### 5. ❌ Message Insertion Position Vague
**Issue**: Tasks.md implied appending, spec requires "immediately after customer's last message"
**Fix**: **T046** now includes explicit algorithm:
```python
# Find customer's last HumanMessage (temporal order)
last_customer_idx = None
for i in range(len(state["messages"]) - 1, -1, -1):
    if isinstance(state["messages"][i], HumanMessage):
        last_customer_idx = i
        break
if last_customer_idx is not None:
    state["messages"].insert(last_customer_idx + 1, synthetic_message)
```

### 6. ❌ Confidence Threshold for Queue Classification Missing
**Issue**: Spec FR-024 requires `confidence < 0.6` default to conservative CONFIRM path
**Fix**: **T031** now includes threshold check:
```python
if classifier_confidence < 0.6:
    has_confirm = True  # Conservative default
    has_cancel = False
    has_modify = False
```

### 7. ❌ Latency Verification Missing
**Issue**: Spec requires p95 < 200ms and queue < 500ms, but no verification tasks
**Fix**: Added **Phase 20.5** (3 new tasks):
- **T074**: Endpoint latency test (p95 < 200ms)
- **T075**: Queue classification latency (< 500ms)
- **T076**: Async safety check via ruff

### 8. ❌ Article V (Strict Async) Enforcement Missing
**Issue**: No explicit check for blocking I/O in event loop
**Fix**: **T076** adds `ruff check --select ASYNC` gate before final verification

### 9. ❌ Confidence/Cost Guard Implementation Vague
**Issue**: Tasks.md didn't separate confidence check from cost check
**Fix**: 
- **T025**: Implement confidence check with threshold (0.7)
- **T026**: Implement cost check using `litellm.token_counter()`
- Both check escalation_count overflow before firing `interrupt()`

### 10. ❌ Task Count & Numbering
**Issue**: Tasks.md had 73 tasks, but added latency verification tasks incomplete
**Fix**: Updated to **80 tasks** (T001–T080):
- T074–T076: Performance verification
- T077–T080: Final verification (renamed from T070–T073)

---

## Mapping to Spec Requirements

| Spec FR | Task | Implementation |
|---------|------|----------------|
| FR-001 (interrupt before order) | T023+T025 | `hitl_guard_node` checks confidence/cost before answer/order logic |
| FR-005 (confidence < 0.7 → HITL) | T025 | `hitl_guard_node` confidence check with 0.7 threshold |
| FR-006 (cost guard) | T026 | `hitl_guard_node` cost check using `litellm.token_counter()` |
| FR-015 (escalation_count cap) | T025 | Overflow check before `interrupt()` call |
| FR-023 (queue_consumer first) | T029–T034 | `queue_consumer_node` runs first on resume |
| FR-024 (queue classification confidence) | T031 | 0.6 threshold check, conservative CONFIRM default |
| FR-025 (structured messages) | T046 | Structured dict format for synthetic messages |
| SC-003 (100% low-confidence escalation) | T025 | All confidence < 0.7 routed to `hitl_guard_node` |

---

## Constitution Compliance

| Article | Issue | Fix |
|---------|-------|-----|
| **V** (Strict Async) | No blocking I/O check | T076: ruff ASYNC linter gate |
| **VI** (Structured Determinism) | Plain string messages | T046: Structured dict + field tracking |
| **VIII** (Human Circuit Breaker) | Dynamic interrupt correctly implements | ✅ No change needed |

---

## Files Updated

- ✅ `tasks.md`: 80 tasks (was 73), fixed node names, added latency verification, fixed synthetic message format
- ✅ `plan.md`: Already correct (dynamic interrupt approach documented)
- ✅ `research.md`: Already correct (decisions ratified)
- 📋 `spec.md`: No change needed (compatible with dynamic interrupt approach via guard node)

---

## Graph Topology (Final)

```
router_node
    ↓
retrieval_node
    ↓
confidence_node
    ├─ (confidence ≥ 0.7) → answer_node → END
    ├─ (confidence < 0.7) → hitl_guard_node
    └─ (cost > 8000) → hitl_guard_node (both paths meet here)

hitl_guard_node (HITL Guard & Pause Logic)
    ├─ (approve resume) → queue_consumer_node
    ├─ (reject) → customer_support_node → END
    └─ (max escalation) → customer_support_node → END

queue_consumer_node (Orphan Tool Scan + Queue Classification)
    ├─ (CANCEL) → cancellation_node → answer_node → END
    ├─ (MODIFY differs) → hitl_guard_node (re-pause)
    ├─ (CONFIRM/OK) → state_freshness_validator_node
    └─ (no messages) → state_freshness_validator_node

state_freshness_validator_node
    ├─ (out-of-stock) → customer_support_node → END
    ├─ (price delta ≥ 5%) → hitl_guard_node (re-pause)
    └─ (OK) → order_execution_node

order_execution_node → answer_node → END
cancellation_node → answer_node → END
customer_support_node → END
```

Total nodes: 11 (5 existing + 6 new)

---

## Performance Requirements (Verified in Phase 20.5)

| Metric | Target | Task |
|--------|--------|------|
| `/review` endpoint p95 latency | < 200ms | T074 |
| `queue_consumer_node` batch classification | < 500ms | T075 |
| Async safety (no blocking I/O) | ✅ Article V | T076 |

---

## Ready to Implement

✅ All 80 tasks coherent, spec-aligned, and sequenced with dependencies.
✅ 4 tasks ready to start immediately: T001, T002, T004, T013
✅ No blocking contradictions between spec and implementation artifacts.
