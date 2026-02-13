# Feature Specification: Core System Foundation & Infrastructure

**Feature Branch**: `001-project-infra-setup`  
**Created**: 2026-02-13  
**Status**: Draft  
**Input**: User description: "WEEK 1 IN @docs/project-log.md. (infra for whole project)"

## Clarifications

### Session 2026-02-13
- Q: How should administrative access (RAG CLI/Endpoints) be authenticated? → A: API Key (environment-variable-backed).
- Q: Where should application logs be directed for collection? → A: Stdout/Stderr (Standard output streams).
- Q: How should tables be organized within the PostgreSQL database? → A: Dedicated Schema (e.g., `agent_v1`).
- Q: How should database schema changes be managed? → A: Alembic (versioned migrations).
- Q: How should application configuration and secrets be managed? → A: Environment Variables (.env).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Project Environment Readiness (Priority: P1)

As a developer, I want to have a standardized and automated environment setup so that I can begin developing features immediately with consistent results across different machines.

**Why this priority**: Essential for team productivity and ensuring that "it works on my machine" translates to "it works everywhere."

**Independent Test**: A single command initializes the entire development environment, installing all necessary runtime components and libraries.

**Acceptance Scenarios**:

1. **Given** a fresh copy of the project, **When** I run the initialization command, **Then** all required dependencies are installed and verified.
2. **Given** the source code, **When** I run the automated quality checks, **Then** the code is validated against formatting and style standards.

---

### User Story 2 - High-Performance Data Foundation (Priority: P1)

As a system architect, I want a robust data storage layer that supports both structured information and advanced search capabilities so that the AI agent has a reliable "brain" for storing product knowledge and customer interactions.

**Why this priority**: The system's ability to "remember" and "search" is the core of the RAG (Retrieval-Augmented Generation) capability.

**Independent Test**: Data can be saved to and retrieved from the storage layer, and semantic similarity searches can be performed on stored information.

**Acceptance Scenarios**:

1. **Given** the storage services are active, **When** I store a piece of information and its vector representation, **Then** I can retrieve it later using both its ID and its meaning (similarity).
2. **Given** a set of initial data, **When** I run the data population script, **Then** the system is ready for use without manual data entry.

---

### User Story 3 - Responsive Service Monitoring (Priority: P2)

As an operator, I want a way to instantly verify the health and responsiveness of the system so that I can ensure the service is available for users.

**Why this priority**: Crucial for operational stability and meeting performance targets.

**Independent Test**: A health check request returns a status update in under 10 milliseconds.

**Acceptance Scenarios**:

1. **Given** the service is running, **When** I request a health status, **Then** I receive an immediate confirmation that all core components are connected and functional.

---

### User Story 4 - Zero-Cost Local Intelligence (Priority: P1)

As a business owner, I want the system to be capable of running its AI functions entirely on local hardware so that development and initial testing can occur without incurring API costs.

**Why this priority**: Directly supports the "Zero-Cost-First" business philosophy and allows for secure, offline development.

**Independent Test**: AI-powered features (like chat or text embedding) function correctly even when the internet connection is disabled.

**Acceptance Scenarios**:

1. **Given** the local AI engine is configured, **When** I interact with the agent, **Then** I receive intelligent responses without any external service calls.

---

### Edge Cases

- **Service Initialization Race Conditions**: How does the application handle situations where the database is not yet fully available at startup?
- **Configuration Errors**: How does the system respond if the local AI engine or storage services are misconfigured?
- **Performance at Scale**: How does the system's responsiveness change as the volume of stored data increases?
- **Partial Offline States**: If DB is up but local AI down, return cached responses with warning.
- **Cache Miss Scenarios**: On semantic cache miss, call AI and store new entry asynchronously.
- **Concurrent AI Requests**: Queue requests with 5s timeout on resource-constrained hardware.
- **Model Version Changes**: Invalidate cache if local embedding model version differs from stored version.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST use an automated dependency management tool that ensures reproducible environments (e.g., `uv`).
- **FR-002**: System MUST utilize Python 3.13+ and implement a fully asynchronous communication pattern for all I/O operations.
- **FR-003**: System MUST provide a relational database foundation that supports vector-based indexing and search (e.g., PostgreSQL with `pgvector`), organized within a dedicated database schema.
- **FR-004**: System MUST implement persistent data structures for: products, text embeddings, semantic response caching, conversation history, and sales intent signals.
- **FR-010**: System MUST use a configuration-based model gateway that allows switching between local and cloud AI providers without code changes (e.g., LiteLLM).
- **FR-011**: System MUST manage database schema changes using a versioned migration tool (e.g., Alembic).
- **FR-012**: System MUST manage all application configuration and sensitive secrets via environment variables, supporting `.env` files for local development.
- **FR-005**: System MUST provide a health check interface with a response time target of < 10ms.
- **FR-006**: System MUST be capable of performing all core AI tasks (text generation and embedding) using local hardware resources.
- **FR-007**: System MUST implement a semantic cache layer that identifies similar previous queries to reduce computational overhead.
- **FR-008**: System MUST produce structured machine-readable logs (JSON) directed to Stdout/Stderr for all significant operations.
- **FR-009**: System MUST provide a command-line interface for administrative data management and search debugging, secured via API Key authentication.
- **FR-013**: System MUST specify minimum hardware requirements for local models: CPU ≥4 cores, RAM ≥8GB, VRAM ≥4GB.
- **FR-014**: System MUST document default local models: embedding=bge-small (v1.5), generation=qwen2.5-3b-instruct-q4.
- **FR-015**: System MUST implement fallback to cloud provider via LiteLLM if Ollama is unreachable, with error logging.
- **FR-016**: System MUST ensure model switching latency <2s between local and cloud providers, measured using Logfire timing metrics.
- **FR-017**: System MUST quantify "100% offline" as: health check, RAG search, semantic cache, text generation.
- **FR-018**: System MUST support configuration via .env files and YAML for LiteLLM.
- **FR-019**: System MUST log significant operations: API requests, DB queries, AI calls, errors.
- **FR-020**: System MUST meet <10ms health check under 1 req/s load.
- **FR-021**: System MUST achieve <5ms semantic search for datasets ≤10k entries.
- **FR-022**: System MUST verify reproducible environments via `uv sync --dry-run` output.
- **FR-023**: System MUST use async wrappers for potentially blocking LiteLLM/Ollama calls.
- **FR-024**: System MUST secure local model endpoints with environment-variable-backed API key.
- **FR-025**: System MUST implement a Tier 1 evaluation runner for RAG search quality using `tests/eval/gold_dataset.json` with HITL grading.
- **FR-026**: System MUST include `source_chunk_ids` (ProductID/ChunkID) in all RAG-generated responses for data provenance.

### Key Entities *(include if feature involves data)*

- **Product Knowledge**: Structured and unstructured data representing the items the agent can sell.
- **Semantic Vector**: A mathematical representation of the meaning of a piece of text.
- **Interaction History**: Persistent record of conversation sessions and messages between the agent and users.
- **Semantic Cache Entry**: A mapping of a query's meaning to its previously generated response.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: System initialization and environment setup can be completed with a single command in under 120 seconds.
- **SC-002**: System health check endpoint responds in less than 10ms under normal conditions (1 req/s load).
- **SC-003**: Semantic similarity search results are returned in under 5ms for existing datasets.
- **SC-004**: 100% of core system functionality (health check, RAG search, semantic cache, text generation) operates without active internet connectivity.
- **SC-005**: All code meets the project's defined formatting and quality standards as verified by automated tools.
