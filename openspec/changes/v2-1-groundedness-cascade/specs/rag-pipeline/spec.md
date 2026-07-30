# rag-pipeline — Spec Delta (v2-1-groundedness-cascade, WP-V2-1)

> Fast-track docs sync (đã implement + đo xong — Tier-F 8/12 → 12/12, Tier-R giữ 34/34).
> Hai requirement mới bổ sung vào `openspec/specs/rag-pipeline/spec.md` khi archive.

## ADDED Requirements

### Requirement: Groundedness self-check before answers are sent
After answer generation in BOTH answer paths (`services/rag/pipeline.answer_with_rag` and the
graph `core/agent/nodes/answer.answer_node`), the pipeline SHALL grade the answer against the
retrieval context with one economy-tier call producing a structured verdict
`{answerable, supported, unsupported_claims}` (`services/rag/groundedness.py`). When
`answerable = false` (the context does not contain the product/info asked about — e.g. an
out-of-catalog subject that slipped past the similarity guard) the pipeline SHALL decline
immediately without regeneration. When `supported = false` (a claim lacks context backing) the
pipeline SHALL regenerate with a stricter grounding prompt up to `GROUNDEDNESS_MAX_REGEN` times
(0..2, default 1) and decline if still unsupported. The check MUST be fail-open: any checker
error, and `GROUNDEDNESS_CHECK_ENABLED = false` (kill switch), restore pre-check behavior
exactly. The verdict SHALL be recorded in `model_traces.metadata` (guard_decision
`GROUNDEDNESS_REJECTED` on decline) and an answer rejected by the verdict MUST NOT be written
to the semantic cache.

**Rationale.** The Layer-1 similarity gate is calibrated per embedding model (0.45 was tuned for
bge-m3's 0.35–0.70 cosine band); higher-scoring embed models (e.g. `local/multilingual-e5-large`)
let off-topic queries through, and similarity alone can never grade generated CLAIMS. The
groundedness verdict is embed-model-independent and closes both gaps (measured 2026-07-30:
Tier-F out_of_catalog 0/4 → 4/4 with zero false declines on answerable cases).

#### Scenario: Out-of-catalog subject is declined despite passing similarity
- **WHEN** the generated answer's verdict has `answerable = false`
- **THEN** the pipeline returns the standardized decline message with `declined = true`, no citations, and the answer is not cached

#### Scenario: Unsupported claim triggers strict regeneration then decline
- **WHEN** the verdict has `supported = false` and regen budget remains
- **THEN** the answer is regenerated with the strict grounding prompt and re-graded; still unsupported after the budget → the pipeline declines

#### Scenario: Kill switch restores pre-check behavior
- **WHEN** `GROUNDEDNESS_CHECK_ENABLED = false`
- **THEN** no verdict call is made and the generated answer is returned exactly as before this change

#### Scenario: Checker outage never blocks answers (fail-open)
- **WHEN** the verdict call errors or returns unparseable output
- **THEN** a passing verdict is assumed and the answer is delivered normally

#### Scenario: SMALLTALK is exempt
- **WHEN** the intent is SMALLTALK (no retrieval context to ground against)
- **THEN** the groundedness check is skipped

### Requirement: Cascade verification for intent escalations
For escalations triggered by intent (COMPLAINT/NEGOTIATION), when `CASCADE_VERIFY_ENABLED = true`
AND the groundedness check is enabled, the graph answer path SHALL generate on the economy tier
first and spend `PREMIUM_MODEL` only when the groundedness verdict fails — the first
regeneration switches to the reserved premium target, and that retry is always budgeted even at
`GROUNDEDNESS_MAX_REGEN = 0`. LOW_CONFIDENCE escalations SHALL keep premium direct (their
trigger already is low confidence — an economy first pass would be wasted). With either switch
off, premium goes direct (pre-change behavior). The cascade decision (`cascade_escalated`) SHALL
be recorded in the trace metadata so premium-call savings are measurable from `model_traces`.

#### Scenario: Grounded economy answer never spends premium
- **WHEN** a COMPLAINT/NEGOTIATION answer generated on economy-chat passes the verdict
- **THEN** the response is sent with `model_used = economy-chat` and no premium call occurs

#### Scenario: Failed verdict escalates to premium
- **WHEN** the economy first pass fails the groundedness verdict
- **THEN** the answer is regenerated on `PREMIUM_MODEL` with the strict prompt and re-graded

#### Scenario: Economy outage falls forward to premium
- **WHEN** the cascade's economy first pass raises
- **THEN** generation retries on the reserved premium target with `escalation_failure = false` (upgrade, not degradation)

#### Scenario: Cascade kill switch restores premium-direct
- **WHEN** `CASCADE_VERIFY_ENABLED = false` (or the groundedness check is disabled)
- **THEN** intent escalations generate on `PREMIUM_MODEL` directly, as before this change
