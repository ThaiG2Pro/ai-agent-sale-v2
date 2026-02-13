# AI Agent Sales System Constitution

## Core Principles

### Article I: Modular Core & Testability

Core business logic MUST be decoupled from the API layer to enable direct testing and debugging.

**Separation of Concerns**:
- Business logic MUST reside in `core/` or `services/` directories
- API/presentation layer (FastAPI routes, Telegram handlers) kept separate in `api/` or `routes/`
- No business logic embedded directly in API endpoint handlers
- Clear boundaries between layers enforced through module structure

**Direct Invocation Requirement**:
- Core functions MUST be callable directly via Python scripts
- Testing and debugging must not require running the full API server
- Example: `uv run python -m core.sales_agent.run_query "product X"` should work without FastAPI


**CLI Interface for RAG & Agent Debugging**:
- The RAG Pipeline (Ingestion & Retrieval) MUST expose a CLI interface for administration and debugging
- CLI required for: document ingestion, vector search testing, embedding generation, index management
- Other features (sales logic, order processing) do NOT require separate CLIs
- RAG CLI must support: text input/output, JSON format, stderr for errors

**Agent Interaction CLI:**
  - A minimal CLI script (e.g., run_agent.py) IS PERMITTED strictly for debugging LangGraph workflows offline.
  - This allows testing conversational logic/state transitions without spinning up the FastAPI server.
  - It must support the "Dev (0 VND)" environment defined in the Techstack.

**Rationale**: For a one-person project, requiring every feature to be a standalone library with CLI is overkill. Developers spend more time writing CLI parsers than core logic. This revision focuses effort where it matters: decoupled, testable core logic with CLI tooling only for the error-prone RAG pipeline that needs frequent debugging.

### Article II: Simplicity and Anti-Abstraction

Combat over-engineering through enforced simplicity constraints.

**Section 2.1: Minimal Project Structure**:
- Maximum 3 projects for initial implementation
- Additional projects require documented justification in Complexity Tracking section
- Justification must include why simpler alternatives are insufficient

**Section 2.2: Framework Trust**:
- Use framework features directly rather than wrapping them
- No Repository patterns unless justified
- No abstraction layers without documented need
- ORM wrappers are discouraged for data fetching but PERMITTED for table definition (Schema) to ensure type safety, PROVIDED that the runtime execution uses optimized async drivers (e.g., SQLAlchemy Core with asyncpg is allowed; ORM Lazy Loading, Session, and Relationship mapping are prohibited).


**Exemption for Orchestration:**
  - LangGraph is explicitly permitted and mandated as the orchestration layer despite its abstraction level.
  - This exemption validates the need for robust state management (persistence) which manual loops cannot easily provide.

**Rationale**: When an LLM might naturally create elaborate abstractions, these articles force it to justify every layer of complexity. Start simple and add complexity only when proven necessary (YAGNI principles).

### Article III: Deterministic TDD & AI Evaluation (NON-NEGOTIABLE)

Testing strategy MUST differentiate between deterministic and non-deterministic components.

**Section 3.1: Deterministic Code Testing**:

For **deterministic components** (SQL queries, API endpoints, utility functions, data transformations):
- MUST follow strict Test-Driven Development (TDD)
- Red-Green-Refactor cycle enforced
- No implementation before tests written, approved, and confirmed to FAIL
- Traditional assertions valid: `assert result == expected_value`

**Section 3.2: Non-Deterministic AI Evaluation**:



For **non-deterministic components** (LLM responses, Agent workflows, RAG retrieval):

**Lean Tiered Evaluation (SME-focused):**

- **Tier 1 — Dev / CI (Deterministic Heuristics):**
  - Automated, zero-cost checks only: keyword presence, JSON schema validation, regex patterns, non-empty and format assertions.
  - Runs fast in CI and must pass for all PRs touching agent logic.

- **Tier 2 — Staging (Human-in-the-loop sample checks):**
  - Do NOT run heavy evaluation frameworks by default. Instead, present a small sampled subset (10–20 critical Golden cases) to a human reviewer via a minimal CLI `evaluation` runner.
  - The runner displays the input, agent response, and suggested heuristic flags; the human assigns a pass/fail or 1–5 confidence score.
  - Staging sign-off requires human review on the sampled set (documented approval), not an automated >70% mandate.

**Process:**
  - Maintain a compact **Gold Dataset** focused on high-risk and high-value cases (10–50 items), curated by product/tech lead.
  - Implement Tier 1 checks as deterministic unit/CI tests (Python + pytest). Fail fast.
  - Provide a minimal `evaluation` CLI for staging that surfaces sampled cases for human grading and stores the results as JSON.
  - Re-run Tier 1 checks automatically on PRs; run the staging evaluation as part of release validation with explicit human approval recorded.

**Rationale:** Large-scale automated LLM grading is heavy and costly for SME projects. A fast, deterministic developer loop plus a compact, human-reviewed staging sample preserves quality while remaining lean and affordable.

**Process Flow**:
1. **Deterministic**: Tests written → User approved → Tests fail → Implement → Tests pass
2. **Non-Deterministic**: Gold dataset created → User approved → Implement → Run evals → Score >70%

**Examples**:
- **Deterministic**: Database query builder, API request validator, price calculator → TDD
- **Non-Deterministic**: Product recommendation agent, question answering system → Evaluation-First

**Rationale**: Traditional TDD is incompatible with LLM outputs because AI results are non-deterministic. You cannot write `assert llm_response == "Hello"`. Distinguishing between deterministic code (strict TDD) and AI components (evaluation-driven) shows engineering maturity and prevents false test failures from stochastic outputs.

### Article IV: Integration-First Testing

Prioritize real-world testing over isolated unit tests.

**Tests MUST use realistic environments**:
- Prefer real databases over mocks
- Use actual service instances over stubs
- Contract tests mandatory before implementation
- Integration tests required for: new library contracts, contract changes, inter-service communication, shared schemas

**Test Hierarchy** (in priority order):
1. Contract tests (API boundaries)
2. Integration tests (real dependencies)
3. Unit tests (only for complex logic)

**Rationale**: This ensures generated code works in practice, not just in theory. Mocks hide integration failures.

### Article V: Asynchronous I/O Mandate

Enforce scalability and responsiveness in the AI Agent architecture.

**Section 5.1: Non-Blocking Core**:

The system creates heavy I/O loads (LLM API calls, database queries). Therefore:
- All Input/Output operations (Database, API, File System) MUST be asynchronous (async/await)
- Synchronous (blocking) drivers (e.g., psycopg2, requests) are STRICTLY PROHIBITED in the runtime path
- asyncpg and httpx (or equivalent async libraries) are the required standards

**Section 5.2: Concurrency Governance**:
- Background tasks (e.g., embedding generation) must not block the main event loop
- Intentional concurrency limits (semaphores) must be applied to external API calls to prevent rate-limit exhaustion
- Use asyncio.gather for parallel operations where appropriate

**Rationale**: An AI Agent spends 90% of its time "waiting" for tokens. Blocking code destroys the system's ability to handle multiple users, rendering it useless for production scaling.

### Article VI: Structured Determinism

Combat the probabilistic nature of LLMs with strict engineering controls.

**Section 6.1: Schema-First Generation**:
- The LLM shall never be asked to output raw text when that text triggers a system action
- All functional outputs MUST use native Structured Output (JSON Mode/Tool Calling) validated against strict Pydantic models
- Regular Expressions (Regex) shall NEVER be used to parse LLM output
- If the output does not fit the Schema, it is treated as an Error/Retry, not a parsing challenge

**Section 6.2: Type Safety Boundaries**:
- Data passing between the LLM and the Application Logic must be Type-Safe
- "String typing" (passing magic strings around) is forbidden
- Use Enums and Data Classes for all structured data
- No loose dictionaries for inter-component communication

**Rationale**: LLMs are creative; Systems must be rigid. This article ensures the "brain" (AI) connects to the "limbs" (Tools) via a rigid joint, preventing runtime crashes due to hallucinated formatting.

### Article VII: Stateless Runtime, Persistent Memory

Ensure the system survives crashes and deployments without data loss.

**Section 7.1: The Disposable Container**:
- The Application Runtime (Docker Container) MUST be stateless
- It must be possible to kill and restart any service instance at any moment without losing the state of an active conversation

**Section 7.2: Externalized State**:
- All conversation state (history, context, variables) MUST be persisted in the Database (Postgres) after every single transition (State Checkpointing)
- In-memory variable storage for conversation state is prohibited
- State persistence must be transactional and atomic

**Rationale**: This supports the "LangGraph" architecture and allows for zero-downtime deployments. If the server restarts while an agent is "thinking", it must resume exactly where it left off.

### Article VIII: The Human Circuit Breaker (Safety Protocol)

Define the absolute boundary of AI autonomy.

**Section 8.1: Critical Action Definition**:

Any action that results in a mutation of external systems is defined as a "Critical Action":
- Creating an order
- Processing refunds or payments
- Sending emails or notifications
- Modifying inventory or pricing
- Any database write affecting business data

**Section 8.2: Mandatory Interruption**:
- The AI Agent is FORBIDDEN from executing Critical Actions autonomously
- The workflow MUST suspend execution (interrupt) and enter an "Awaiting Approval" state before a Critical Action node
- Only a cryptographically authenticated signal from a Human (Admin/User) can resume the workflow
- Approval requests must clearly display the action and its consequences

**Rationale**: This enforces the "SME-Friendly" and "Safety" goals. It prevents the nightmare scenario of an AI hallucinating a discount or accidentally spamming orders.

### Article IX: Data Provenance & Hallucination Zero

Enforce honesty in Retrieval Augmented Generation (RAG).

**Section 9.1: Citation Requirement**:
- Every assertion of fact made by the Agent regarding products or inventory MUST be accompanied by a reference to the source data (Product ID, Document Chunk ID)
- Responses must include metadata showing where information came from

**Section 9.2: The "I Don't Know" Fallback**:
- If the Vector Search or SQL Query returns insufficient confidence scores, the Agent MUST explicitly state it cannot find the information
- Using internal LLM training data to fill gaps in specific domain queries (Inventory, Price, Business Rules) is STRICTLY PROHIBITED
- Unknown is better than incorrect

**Section 9.3: Confidence Thresholds**:
- Vector search results below 0.7 similarity must trigger "insufficient data" response
- Database queries returning zero results must be honestly reported, not filled with guesses

**Rationale**: For a Sales Agent, a wrong price is worse than no answer. This forces the system to rely only on the provided database, establishing trust with the business owner.

### Article X: The Frugal Architect (Cost Management)

The system is designed for cost-efficiency suitable for a solo developer with limited budget.

**Section 10.1: Token Economy**:
- All LLM interactions MUST implement token usage tracking and logging
- Token counts must be logged with each request: prompt_tokens, completion_tokens, total_tokens
- Monthly token budgets should be monitored and enforced
- "Chatty" loops are STRICTLY PROHIBITED
- Agent recursion limits MUST be set (default: maximum 5 turns per conversation)
- Infinite loops or unbounded agent iterations are forbidden

**Section 10.2: Model Selection Strategy**:

**Economy Tier:** Llama-3, Qwen2.5, Qwen3, . . .
  - Dùng cho: Intent classification and routing, Summarization tasks, Simple query reformulation, Validation checks.

**Premium Tier:** GPT-4, Claude 3.5 Sonnet, hoặc Llama-3-70B, . . .
  - Dùng cho: Complex reasoning requiring deep understanding, Multi-step problem solving, Critical business decisions.

Model selection must be justified and documented in code.

**Section 10.3: Caching First**:
- Aggressive caching policies MUST be implemented for:
  - Embedding generation (cache document embeddings, never regenerate)
  - LLM responses for exact query matches (deterministic prompts)
  - Vector search results (cache with TTL for frequently accessed queries)
- Cache invalidation strategy required for data updates
- Cache hit rate should be monitored and optimized

**Rationale**: API bills can spiral out of control with careless LLM usage. A solo developer cannot afford $500/month for testing. Token tracking, recursion limits, and caching ensure the system stays within budget while maintaining functionality. Every token has a cost.

### Article XI: Documentation as Code (Extended Memory)

Documentation serves as external memory for a solo developer working across multiple features over weeks or months.

**Section 11.1: Business Intent Docstrings**:
- Docstrings are MANDATORY for all public modules, classes, and functions
- Docstrings must explain the **"Why"** (business intent), not just the **"What"** (code description)
- Template: `"""Why this exists: [Business problem]. What it does: [Brief summary]."""`
- Focus: Future-you (in 3 months) should understand the business context without reading all the code

**Section 11.2: Architecture Decision Records (ADR)**:
- Any major technical choice MUST be recorded in `/docs/adr/` folder
- ADR required for:
  - Framework selection (e.g., "Why LangGraph over LangChain")
  - Database schema decisions (e.g., "Why embedding dimension 1536")
  - Model selection (e.g., "Why Claude over GPT-4")
  - Architecture patterns (e.g., "Why state machine over simple chain")
- ADR format: Context → Decision → Consequences → Alternatives Considered
- ADRs are immutable once written (create new ADR to reverse, don't edit)

**Section 11.3: Inline Business Comments**:
- Complex business logic (not just code complexity) requires inline comments
- Example: `# Discount applies only to users who made 3+ purchases in last 30 days (per CEO request 2026-01-15)`
- Comments explain business rules, not syntax

**Rationale**: Solo developers context-switch frequently and lose context. Returning to code after 2 weeks feels like someone else wrote it. Documenting "Why" prevents re-learning business logic. ADRs prevent repeating architectural debates. Good documentation is cheaper than re-reverse-engineering decisions.


### Article XII: The Efficiency Metric (Cost-Awareness)

#### Adaptive Logic Evaluation

Testing MUST verify not just the quality of the answer, but the route taken.

- "Easy" queries in the Gold Dataset MUST assert that model_used was the cheap model.
- "Hard" queries MUST assert that escalation occurred.
- Failure to escalate on complex queries OR unnecessary escalation on simple queries constitutes a test failure.

**Section 12.1: Evaluation Beyond Accuracy**:
Testing must verify not just *correctness*, but *efficiency*.
- **Metric**: `Efficiency Score = Accuracy / Cost_Per_Token`
- **Escalation Constraint**: Test cases categorized as "Simple" MUST fail if the Agent escalates to a Premium Model, even if the answer is correct.

**Section 12.2: Zero-Cost Baseline**:
- The system must be fully functional (including observability and caching) in an offline, local environment without paid SaaS API keys (except LLM inference).



## Architecture Constraints

### Observability Requirements

**Text I/O Protocol**:
- RAG CLI interactions must be traceable through text logs
- Structured logging (JSON) required for all operational events
- Log levels: DEBUG (development), INFO (business events), WARN (recoverable errors), ERROR (failures)

**Debugging Support**:
- RAG CLI commands must support --verbose flag for detailed output
- All async operations must be instrumented with timing metrics
- LLM interactions must log: prompt, response, tokens used, latency

### Versioning & Breaking Changes

**Version Format**: MAJOR.MINOR.PATCH
- **MAJOR**: Breaking changes to contracts, CLI interfaces, or core principles
- **MINOR**: New features, new libraries, backward-compatible additions
- **PATCH**: Bug fixes, performance improvements, documentation

**Breaking Change Protocol**:
- Breaking changes require constitution amendment
- Migration guide mandatory for major version bumps
- Deprecated features must be marked for at least one minor version before removal

## Governance

**Constitution Supremacy**:
- This constitution supersedes all other development practices, coding standards, or team preferences
- All implementation plans, code reviews, and pull requests MUST verify compliance with these articles
- Violations require explicit justification in the Complexity Tracking section

**Amendment Process**:
1. Proposed changes must be documented with rationale
2. Impact analysis on existing templates and code
3. Version bump according to semantic versioning
4. Update all dependent templates and documentation
5. Migration plan for existing code (if needed)

**Compliance Review**:
- Every feature specification must reference relevant constitutional articles
- Implementation plans must include Constitution Check gates
- Code reviews verify adherence to testing strategies (Article III), async patterns (Article V), and type safety (Article VI)
- Critical Actions (Article VIII) must be audited in every release

**Template Synchronization**:
- Changes to principles must propagate to: plan-template.md, spec-template.md, tasks-template.md
- All templates must remain consistent with constitutional requirements
- Agent instruction files must reference this constitution as the source of truth

**Version**: 1.2.0 | **Ratified**: 2026-01-29 | **Last Amended**: 2026-01-29
