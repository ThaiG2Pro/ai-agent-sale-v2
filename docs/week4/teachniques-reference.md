# Technical Reference - Week 4 AI Agents

## 1. HITL & Transaction Safety (LangGraph + Postgres)
- **Methods:**
    - `.compile(checkpointer=saver, interrupt_before=["node_name"], interrupt_after=["node_name"])`: Static breakpoints configuration.
    - `interrupt()`: Dynamic breakpoint within node logic; throws `GraphInterrupt`.
    - `graph.invoke(Command(resume=value), config=config)`: Resuming graph execution with human input.
    - `graph.get_state(config)`: Retrieving current state snapshot and `next` executable nodes.
- **Components:**
    - `AsyncPostgresSaver`: Persistent checkpointer using `psycopg3`.
    - **Database Tables:** `checkpoints` (snapshots), `checkpoint_blobs` (serialized data), `checkpoint_writes` (intermediate writes), `checkpoint_migrations`.
- **Syntax:**
    - `JsonPlusSerializer(pickle_fallback=False)`: Required to prevent RCE vulnerabilities (CVE-2026-27794).
    - `thread_id`: Persistent cursor for session management and context reloading.
- **Keywords:** HITL, Persistence, Dehydration/Rehydration, Idempotency, `thread_id`, Order Safety.
- **Conclusion:** HITL mechanisms ensure irreversible actions (e.g., payments) are validated by humans while maintaining system resilience and auditability.

## 2. Transparent State Inspection
- **Methods:**
    - `graph.aget_state(config)`: Asynchronous retrieval of `StateSnapshot`.
- **Components:**
    - `StateSnapshot`: Unit of inspection containing `values`, `next` (nodes list for parallel processing), `config`, `metadata`, `tasks`.
    - `AsyncPostgresSaver`: Configured with `autocommit=True` and `row_factory=dict_row`.
- **Syntax:**
    - `dict_row`: Ensures data access by column name in complex snapshots.
    - Connection Pooling: `AsyncConnectionPool` for high-concurrency state access (< 10ms latency).
- **Keywords:** Snapshot, RBAC, Sales Intent (Budget, Urgency, Product List), PII Sanitization.
- **Conclusion:** Transparent state access allows real-time human oversight and "sales intent" extraction without disrupting AI autonomy.

## 3. Idempotent Review Gateway
- **Methods:**
    - `interrupt()`: Programmatic halt within node logic.
    - `Command(resume=value)`: Resuming with payload injection.
    - `update_state(config, values)`: Modifying state before resuming.
- **Components:**
    - `Redis` (via `RedisSaver`/`AsyncRedisSaver`): Recommended for short-term, low-latency thread storage.
    - `Idempotency-Key` (X-Request-ID): Prevents duplicate processing of human reviews.
- **Syntax:**
    - $S_{t+1} = \Phi(S_{t}, C_{resume})$: State transition model for re-executing nodes.
    - `escapeRediSearchTagValue()`: Mandatory utility to prevent tag injection (CVE-2026-27022).
    - `X-Idempotency-Status`: Header indicating if request is "hit" or "new".
- **Keywords:** Idempotency, Time Travel, Distributed Locking, RedisSearch Injection.
- **Conclusion:** Idempotent gateways ensure that human-in-the-loop interactions are stable against network retries and concurrent execution conflicts.

## 4. State Override & Data Integrity
- **Methods:**
    - `graph.update_state(config, values, as_node="node_name")`: Overriding state values while preserving history.
- **Components:**
    - `Pydantic`: Schema validation for state overrides (Input Sanitization).
    - `PostgresSaver`: Stores Audit Trails via JSONB metadata.
- **Syntax:**
    - `as_node`: Identifies the update source (AI vs. Admin) to trigger correct graph edges.
    - **Update Patterns:**
        - **Pattern A (Fork):** Re-executing from parent checkpoint.
        - **Pattern B (Resume with Update):** Atomic update + resume using `Command`.
        - **Pattern C (Time Travel):** Branching from a historical checkpoint.
- **Keywords:** Single Source of Truth, Immutability, Audit Log, State Injection, Value-only Updates.
- **Conclusion:** Controlled state overrides allow human correction of AI logic while maintaining a verifiable audit trail of all changes.

## 5. Confidence Guard & Escalation
- **Methods:**
    - `Reciprocal Rank Fusion (RRF)`: Merging vector and lexical search results.
    - `interrupt()`: Triggered when confidence metrics fall below thresholds.
- **Components:**
    - **Hybrid Retrieval:** Vector search + BM25.
    - **RAG Triad:** Context Relevancy, Faithfulness, Answer Relevancy.
    - **OOD Detection:** Identifying out-of-distribution queries (98.3% accuracy).
- **Syntax:**
    - **RRF Formula:** $score_{RRF}(d) = \sum_{r \in R} \frac{1}{k + rank_r(d)}$ ($k=60$).
    - **Thresholds:** Faithfulness $\ge 0.9$, Context Relevancy $\ge 0.7$, Answer Relevancy $\ge 0.7$.
- **Keywords:** Hybrid Retrieval, RRF, RAG Triad, OOD Detection, Confidence-Driven Routing.
- **Conclusion:** Confidence guards prevent AI hallucinations by validating retrieval quality and intent before generating responses.

## 6. Cost Guard (FinOps)
- **Methods:**
    - `litellm.token_counter(model, messages)`: Precise input token counting.
    - `litellm.completion_cost()`: Calculating actual cost of API calls.
    - `interrupt()`: Circuit breaker for high-cost requests.
- **Components:**
    - `LiteLLM`: Model cost management and multi-provider abstraction.
    - `Guard Node`: LangGraph node placed after Retrieval and before Generation.
- **Syntax:**
    - **Budget Guidance Formula:** $p(Y_t | X, Y_{<t}, L_t \leq \bar{l} - t) \propto p(Y_t | X, Y_{<t}) \cdot Pr(L_t \leq \bar{l} - t | X, Y_{<t}, Y_t)$
    - Completion Estimate: Core Answer $\approx 42\%$ of total response tokens.
- **Keywords:** Economic Escalation, Token Bloat, Context Injection, Reasoning Loops, Circuit Breaker.
- **Conclusion:** Cost guards protect SME budgets by enforcing token limits and requiring human approval for expensive reasoning steps.

## 7. Dynamic Escalation Node
- **Methods:**
    - `Command(goto="node", update={"active_model": "model_id"})`: Dynamic routing to premium models.
- **Components:**
    - `LiteLLM Router`: Centralized gateway for load balancing and fallbacks.
- **Syntax:**
    - **Confidence Score (Logprobs):** $Confidence = \exp\left(\frac{1}{n} \sum_{i=1}^{n} \text{logprob}_i\right)$
    - **Routing Tiers:** $>0.85$ (SLM), $0.65-0.85$ (Mid-tier), $<0.65$ (Frontier/Human).
- **Keywords:** Unit Economics Triage, Logprobs, Self-REF, EDoS, Fallback.
- **Conclusion:** Dynamic escalation optimizes model usage by reserving expensive frontier models for high-risk intents (Complaints, Negotiations) and low-confidence queries.

## 8. Confidence Scoring & Hallucination Guard
- **Methods:**
    - `calculate_confidence()`: Hybrid formula combining similarity and rerank scores.
    - `Min-Max Scaling`: Normalizing disparate score scales to $[0, 1]$.
- **Components:**
    - `pgvector`: Vector distance calculation (Cosine Similarity).
    - `Reranker API` (Cross-Encoders): Contextual relevance validation via cross-attention.
- **Syntax:**
    - **Cosine Similarity:** $C(A, B) = \frac{A \cdot B}{\|A\| \|B\|}$
    - **Confidence Fusion:** $Confidence = (1 - \alpha) \cdot Similarity_{norm} + \alpha \cdot Rerank_{norm}$ ($\alpha = 0.7$).
    - **Sigmoid Normalization:** $\sigma(x) = \frac{1}{1 + e^{-x}}$
- **Keywords:** Similarity Score, Rerank Score, DIG (Document Information Gain), Sweet Spot ($0.65-0.75$).
- **Conclusion:** Integrating multi-layered scoring into the Graph State ensures AI responses are grounded in verified knowledge sources.

## 9. Tool Contract Testing
- **Methods:**
    - `fetch_inventory(product_id: str)`: Mock tool for verification.
    - `respx.mock`: Mocking HTTP transport layer for external APIs.
- **Components:**
    - `pytest-asyncio`: Framework for testing asynchronous tool logic.
    - `Pydantic v2`: Strict/Lax mode validation for interface integrity.
- **Syntax:**
    - **Success Probability:** $P(\text{success}) = 1 - P(\text{fail})^n$ (Retry logic verification).
    - `SecretStr`: Pydantic type to prevent key leakage in logs.
    - `SensitiveLogFilter`: Redacting PII/Keys from output.
- **Keywords:** TDD, Consumer-driven contract testing, Schema Drift, Tool Argument Injection, Mock Rot.
- **Conclusion:** Contract testing provides a rigid boundary for AI-tool interactions, ensuring system integrity against model hallucinations and API changes.
