# cost-governance — Spec Delta (v2-5-cost-dashboard, WP-V2-5)

> Fast-track docs sync (đã implement + đo — unit 472 pass, Tier-R 34/34 giữ nguyên, endpoint
> verify khớp raw SQL trên dev DB). Ba requirement ADDED cho ops surface + answer path.

## ADDED Requirements

### Requirement: SME reads real spend with one curl (cost dashboard)

The system SHALL expose `GET /admin/costs?from=&to=&group_by=day|customer|model` behind the
admin key, aggregating `model_traces` into: call count, prompt/completion/total tokens, cost
USD, latency p50/p95, cache hits and cache hit-rate — per group and as totals. Default range
SHALL be the last 7 days (UTC). `group_by=customer` SHALL key on `metadata->>'customer_id'`
(stamped by answer_node since V2-5; older rows group under "unknown"). Invalid `group_by` or
an inverted range SHALL return 400; a missing/wrong admin key SHALL return 401.

#### Scenario: Costs by model over the default week

- **WHEN** `curl -H "X-Admin-Key: …" /admin/costs?group_by=model` runs
- **THEN** the response totals match `SELECT count(*), sum(cost), sum(total_tokens) FROM
  model_traces` over the same range, with per-model groups and latency percentiles

#### Scenario: Unauthorized access is rejected

- **WHEN** `/admin/costs` is called without `X-Admin-Key`
- **THEN** the response is 401 and no data is returned

### Requirement: Daily budget ceiling downgrades, never blocks

When `DAILY_COST_LIMIT_USD > 0` and today's (UTC) summed `model_traces.cost` has reached the
limit, the answer path SHALL force the LLM call down to `light-chat`, log a warning, and mark
the trace metadata with `budget_downgrade=true`; the cascade premium reserve SHALL be
disabled for that turn. The guard SHALL fail OPEN on DB errors (a broken meter must not stop
sales) and SHALL be a complete no-op — zero extra queries — at the default `0` (off).

#### Scenario: Over budget → light model, still answered

- **GIVEN** `DAILY_COST_LIMIT_USD=5` and today's traces already sum to $5.01
- **WHEN** a PRICING query reaches answer_node
- **THEN** the answer is generated on `light-chat` and the trace carries
  `budget_downgrade=true` — the customer is never refused for budget reasons

### Requirement: Per-customer daily cap stops single-customer burn

When `CUSTOMER_DAILY_MSG_CAP > 0` and one customer's LLM-path turns today (counted via
`metadata->>'customer_id'`) have reached the cap, the answer path SHALL return a polite
come-back-later message WITHOUT calling the LLM, writing a `CUSTOMER_CAP` trace. Free paths
(cache hits, business responses, declines) SHALL NOT be capped. Additionally, SMALLTALK SHALL
route to `light-chat` (`CHEAP_INTENT_LIGHT_ROUTING=false` restores pre-V2-5 `economy-chat`).

#### Scenario: Capped customer gets a polite hold, not silence

- **GIVEN** `CUSTOMER_DAILY_MSG_CAP=30` and cust_1 already has 30 LLM turns today
- **WHEN** cust_1 asks another question
- **THEN** the polite cap message is returned, no LLM call is made, and the trace records
  guard_decision `CUSTOMER_CAP`

#### Scenario: SMALLTALK answers on the light tier

- **WHEN** a greeting ("xin chào") reaches answer_node
- **THEN** the reply is generated on `light-chat` instead of `economy-chat`
