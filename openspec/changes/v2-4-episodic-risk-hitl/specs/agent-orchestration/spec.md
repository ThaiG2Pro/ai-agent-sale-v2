# agent-orchestration — Spec Delta (v2-4-episodic-risk-hitl, WP-V2-4)

> Fast-track docs sync (đã implement + đo — unit 457 pass, Tier-R 34/34, Tier-F 12/12 giữ
> nguyên). Hai requirement ADDED cho graph LangGraph + tầng memory.

## ADDED Requirements

### Requirement: Time-referenced queries recall episodic memory

The system SHALL maintain an append-only episodic event log (`episodic_events`) recording,
per accepted answer turn: the customer's message, a response summary, the intent, the
products cited, and a timestamp — strictly scoped by `customer_id`. `answer_node` SHALL
append events best-effort (never failing the response) for business, cache-hit, and
accepted-LLM paths, skipping SMALLTALK and declined turns. When a query contains a
Vietnamese time reference ("hôm qua", "lần trước", "bữa trước", "tuần trước", "đã tư vấn",
…), `memory_retrieval_node` SHALL load the customer's most recent `EPISODIC_RECENT_LIMIT`
events into `memory_context` (source="episodic"), enabling the existing memory-override of
declines. Episodic data SHALL be exposed at `GET /memory/episodic/{customer_id}?limit=`
behind the admin key, and SHALL be deleted by the RTBF cascade
(`DELETE /memory/customer/{customer_id}`). `EPISODIC_MEMORY_ENABLED=False` SHALL disable
both writes and reads (kill switch).

#### Scenario: "Cái máy hôm qua em tư vấn ấy" is answered from episodic memory

- **GIVEN** yesterday's turn recorded an episodic event citing "Dell XPS 15" for cust_1
- **WHEN** cust_1 asks "cái máy hôm qua em tư vấn ấy còn hàng không"
- **THEN** memory_retrieval_node appends the event line (timestamp + products) to
  memory_context and the turn is not declined for low confidence

#### Scenario: RTBF wipes the episodic layer

- **WHEN** `DELETE /memory/customer/cust_1?confirm=true` runs
- **THEN** the deletion report includes `episodic_events` and all of cust_1's events are
  removed (8-table cascade)

#### Scenario: Kill switch restores pre-V2-4 memory behavior

- **GIVEN** `EPISODIC_MEMORY_ENABLED=False`
- **WHEN** any turn completes or a time-referenced query arrives
- **THEN** no episodic rows are written and none are read (semantic memory only)

### Requirement: HITL triggers by composite risk score in three tiers

`hitl_guard_node` SHALL compute
`risk = W_CONF*(1-confidence) + W_VALUE*order_value_norm + W_HISTORY*history_factor`, where
`order_value_norm` applies only to ORDER_PLACEMENT (missing/unparseable order value = 1.0,
conservative) and `history_factor` derives from `intent_tracking`
(CONVERTED=0.0, ENGAGED/AWAITING_QUOTE/CONTACTED=0.5, NEW/LOST=0.8, unknown customer or DB
error=1.0). Tier 1 (risk < `HITL_RISK_TIER1_THRESHOLD`) SHALL auto-proceed — an order flows
to `queue_consumer_node` with `hitl_approved=True`; Tier 2 SHALL interrupt exactly as
pre-V2-4; Tier 3 (risk >= `HITL_RISK_TIER3_THRESHOLD`) SHALL route directly to
`customer_support_node`. SAFETY INVARIANT (non-configurable): an ORDER_PLACEMENT whose value
exceeds `HITL_HIGH_VALUE_ORDER_THRESHOLD` — or whose value is unknown — SHALL always be at
least Tier 2; no weight or threshold tuning can auto-approve it. The token cost guard and
escalation-limit guard SHALL remain active for all tiers. `RISK_HITL_ENABLED=False` SHALL
restore the pre-V2-4 binary triggers (kill switch).

#### Scenario: Small order from a returning customer auto-proceeds

- **GIVEN** ORDER_PLACEMENT of 200,000 VND, confidence 0.95, customer with a CONVERTED
  intent_tracking row
- **WHEN** hitl_guard_node runs
- **THEN** no interrupt fires and the order proceeds to queue_consumer_node with
  `hitl_approved=True`

#### Scenario: High-value order always waits for a human

- **GIVEN** ORDER_PLACEMENT of 10,000,000 VND (> HITL_HIGH_VALUE_ORDER_THRESHOLD) from the
  same low-risk customer
- **WHEN** hitl_guard_node runs
- **THEN** interrupt() fires with reason ORDER_APPROVAL (Tier >= 2 by invariant)

#### Scenario: Worst-case risk goes straight to support

- **GIVEN** ORDER_PLACEMENT of 50,000,000 VND, confidence 0.1, unknown customer (risk >= 0.75)
- **WHEN** hitl_guard_node runs
- **THEN** the turn routes to customer_support_node with reason `high_risk_tier3` and no
  interrupt fires

#### Scenario: Kill switch restores binary triggers

- **GIVEN** `RISK_HITL_ENABLED=False`
- **WHEN** any ORDER_PLACEMENT arrives (even 200,000 VND from a CONVERTED customer)
- **THEN** interrupt() fires exactly as pre-V2-4
