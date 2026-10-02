# Feature Specification: Vietnamese RAG & Evaluation

**Feature Branch**: `002-vietnamese-rag-eval`
**Created**: 2026-02-25
**Status**: Draft
**Input**: User description: "Week 2 — Vietnamese RAG pipeline with hybrid retrieval, adaptive context building, confidence gating, and evaluation CLI for the SME AI Sales Agent."

---

## Clarifications

### Session 2026-02-26

- Q1: Hybrid fusion algorithm → A: Reciprocal Rank Fusion (RRF) with k=60: `final_score = 1 / (60 + rank_vector) + 1 / (60 + rank_fts)`. Chunks missing from one source get max_rank + 1.
- Q2: Query classification for ambiguous → A: Hybrid heuristic: >15 words AND (no specific product name OR no action verb like 'price'/'compare') → ambiguous.
- Q3: Edge case handling → A: All 5 specified edge cases require explicit error handling: (1) embedding unavailable, (2) mixed VN/EN, (3) zero results both methods, (4) >500 token query, (5) compression reduces to nothing.
- Q4: Evaluation grading scale → A: 5-point Likert (1=completely wrong, 5=perfectly accurate with citations). Aggregate score = avg across all queries.
- Q5: Context compression beyond score < 0.5 → A: Add near-duplicate removal (>80% overlap with higher-scoring chunk). Score-only + dedup + near-dup removal for Week 2.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Vietnamese Product Query Answering (Priority: P1)

A Vietnamese-speaking customer asks a question about a product (e.g., "Giá sản phẩm X là bao nhiêu?" or "Sản phẩm Y có những tính năng gì?"). The system retrieves the most relevant product chunks, compresses the context, and returns a grounded, cited answer.

**Why this priority**: This is the core value of the RAG pipeline. Without accurate retrieval and answer generation, no downstream feature is meaningful.

**Independent Test**: Can be tested end-to-end by sending a Vietnamese product query and verifying the returned answer references the correct product ID and chunk ID. Delivers a working RAG loop as an MVP.

**Acceptance Scenarios**:

1. **Given** a product database is populated and embeddings are indexed, **When** a user sends a Vietnamese product query, **Then** the system returns an answer that cites at least one ProductID and ChunkID from the database.
2. **Given** a query that matches a product in the database, **When** the system retrieves and compresses context, **Then** the final answer is generated without duplicate or low-signal content being passed to the language model.
3. **Given** a query with a short text (1–5 words), **When** the system processes it, **Then** the number of retrieved results is 5 (short-query TopK).

---

### User Story 2 - Hybrid Search Outperforms Vector-Only (Priority: P2)

A developer validates that the hybrid retrieval strategy (semantic vector search combined with full-text search) returns more relevant results than either approach alone for Vietnamese product queries.

**Why this priority**: Hybrid search is the key retrieval quality lever. Vietnamese language has specific tokenization characteristics that make keyword matching valuable alongside semantic search.

**Independent Test**: Run the same set of Vietnamese queries using vector-only vs. hybrid mode and compare recall. Hybrid must outperform on at least the majority of test queries.

**Acceptance Scenarios**:

1. **Given** a set of ≥10 Vietnamese test queries, **When** hybrid search is run vs. vector-only, **Then** hybrid search returns higher recall (more relevant documents in top results) on the majority of queries.
2. **Given** a query containing specific Vietnamese product keywords, **When** the system performs FTS alongside vector search, **Then** exact keyword matches surface in the result set even when semantic similarity is moderate.

---

### User Story 3 - Confidence-Gated Responses (Priority: P3)

When a user asks a question that does not match any stored product information with sufficient confidence, the system honestly declines to answer rather than generating a hallucinated response.

**Why this priority**: Hallucination is the number-one trust-destroyer in SME AI deployments. A system that says "I don't know" is safer than one that fabricates product details.

**Independent Test**: Send a query about a product that does not exist in the database and verify the response is a polite decline, not a fabricated answer.

**Acceptance Scenarios**:

1. **Given** a query about a product not in the database, **When** all retrieved chunks have similarity score < 0.7, **Then** the system returns a standardized "I couldn't find relevant information" message and does NOT generate a product answer.
2. **Given** a borderline query where best chunk similarity is exactly 0.7, **When** the threshold is set to 0.7, **Then** the system proceeds to answer (boundary inclusive).
3. **Given** a query that triggers the confidence guard, **When** the guard fires, **Then** the similarity score and guard-trigger flag are recorded in state for observability.

---

### User Story 4 - Adaptive Context & TopK (Priority: P4)

The system automatically adjusts how many results it retrieves and how it ranks them based on the nature of the query, ensuring cost efficiency for simple queries and thoroughness for complex ones.

**Why this priority**: Static TopK wastes tokens on simple queries and misses relevant context on complex ones. Adaptive TopK directly controls LLM cost.

**Independent Test**: Submit queries of varying lengths/complexity and verify the TopK value changes accordingly (short → 5, long → 15, ambiguous → 20).

**Acceptance Scenarios**:

1. **Given** a short query (≤5 words), **When** the system computes TopK, **Then** it retrieves exactly 5 chunks.
2. **Given** a long, multi-sentence query, **When** the system computes TopK, **Then** it retrieves up to 15 chunks.
3. **Given** an ambiguous or open-ended query (e.g., "Tell me about your products"), **When** the system classifies it as ambiguous, **Then** it retrieves up to 20 chunks.
4. **Given** any query, **When** context is assembled, **Then** duplicate chunks are removed and low-signal text is stripped before reaching the language model.

---

### User Story 5 - Evaluation CLI for Human Grading (Priority: P5)

A developer or QA engineer runs an evaluation CLI that presents RAG responses for a gold dataset of Vietnamese queries, records human grades, and outputs a quality score.

**Why this priority**: Evaluation without tooling is opinion. A CLI that captures human grades on a gold dataset turns RAG quality into a measurable, improvable number.

**Independent Test**: Run `python scripts/tier1_eval.py` with a ≥10-query Vietnamese gold dataset. CLI presents each query/answer pair for human grading and outputs a final score report.

**Acceptance Scenarios**:

1. **Given** a gold dataset of ≥10 Vietnamese queries with expected answers, **When** the evaluation CLI is run, **Then** it displays each query, the RAG-generated answer, and the retrieved citations for human review.
2. **Given** a human reviewer grades an answer (e.g., pass/fail or 1–5 score), **When** all queries are graded, **Then** the CLI outputs an aggregate quality score and per-query breakdown.
3. **Given** a metadata-enriched answer, **When** the evaluator reviews it, **Then** each answer includes the ProductID and ChunkID used to generate it.

---

### Edge Cases

- What happens when the embedding model is unavailable at query time?
- How does the system handle queries with mixed Vietnamese and English text?
- What happens when a query returns 0 results from both vector and FTS search?
- How are extremely long queries (>500 tokens) handled by adaptive TopK?
- What happens if context compression reduces chunks to nothing (all low-signal)?

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST convert any text input into a fixed-dimension vector embedding using a single, configured embedding model per environment.
- **FR-002**: The system MUST perform asynchronous vector search returning TopK ranked results from the product embedding store.
- **FR-003**: The system MUST extract at least 5 keywords/metadata fields per product chunk during ingestion for use in full-text search.
- **FR-004**: The system MUST normalize user queries (Vietnamese text cleaning, intent extraction) into a structured format using a language model and Pydantic schema — no regex parsing.
- **FR-005**: The system MUST perform hybrid retrieval combining vector similarity search and full-text search, producing a unified ranked result set using Reciprocal Rank Fusion (RRF): `final_score = 1 / (60 + rank_vector) + 1 / (60 + rank_fts)`. Chunks appearing in only one source are assigned max_rank + 1 for the missing source. Hybrid retrieval MUST achieve higher recall than vector-only search on at least 70% of representative queries.
- **FR-006**: The system MUST support a Vietnamese gold evaluation dataset of at least 10 real-world product queries stored as JSON.
- **FR-007**: The system MUST implement a complete RAG flow: query rewrite → hybrid search → context compression → answer generation.
- **FR-008**: The system MUST provide a CLI evaluation runner that presents query/answer/citation triples for human grading on a 5-point Likert scale (1=completely wrong, 5=perfectly accurate with complete citations) and outputs aggregate and per-query scores.
- **FR-009**: The system MUST compute TopK dynamically based on query characteristics: short queries (≤5 words) → TopK 5, long queries (6–15 words) → TopK 15, ambiguous queries (>15 words AND lacking specific product name/action verb) → TopK 20.
- **FR-010**: The system MUST record a similarity gap score (best match score vs. threshold) for every query and store it in the query state.
- **FR-011**: Every answer generated MUST include citation metadata mapping each answer fragment to its source ProductID and ChunkID.
- **FR-012**: The system MUST compress retrieved chunks before passing context to the language model by: (a) removing exact text duplicates, (b) removing chunks with retrieval score < 0.5, and (c) removing near-duplicate chunks (>80% text overlap with a higher-scoring chunk). Only distinct, high-confidence chunks are retained.
- **FR-013**: The system MUST enforce a confidence threshold guard: if the best similarity score across all retrieved chunks is below 0.7, the system returns a predefined decline message instead of generating an answer.
- **FR-014**: The embedding model name, version, and vector dimension MUST be stored alongside every embedding record in the database to prevent mixing incompatible embeddings.
- **FR-015**: The system MUST classify every user query into one of three categories—**short** (≤5 words), **long** (6–15 words), or **ambiguous** (>15 words AND lacking specific product name OR action verb)—and assign TopK accordingly (5, 15, 20). Classification criteria MUST be deterministic and based on word count and presence of interrogative keywords/action verbs, not ML-based intent detection.
- **FR-016** (Edge Cases): The system MUST handle five specified edge cases with explicit error behavior: (1) Embedding model unavailable → return "Service unavailable" message; (2) Mixed Vietnamese/English query → process as-is with unified embedding; (3) Zero results from both vector and FTS → return confidence guard decline message; (4) Query >500 tokens → truncate to 500 and compute TopK; (5) Context compression reduces chunks to empty → return confidence guard decline message.

### Key Entities

- **Query**: A normalized Vietnamese user question, including raw text, canonical form, computed TopK, and similarity gap score.
- **Chunk**: A segment of product content with text, vector embedding, keyword metadata, ProductID, and ChunkID references.
- **RetrievalResult**: A ranked list of chunks returned from hybrid search, each with vector score, FTS score, and combined rank.
- **RAGAnswer**: The final answer object containing response text, source citations (ProductID + ChunkID), confidence score, model used, and escalation flag.
- **EvaluationRecord**: A gold dataset entry pairing a Vietnamese query with an expected answer, plus human grade and RAG-generated answer for comparison.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Hybrid retrieval (RRF-based) achieves higher recall than vector-only search on at least 70% of a gold Vietnamese query set of ≥10 distinct queries (where recall is defined as: number of relevant chunks in top-20 RRF results / total relevant chunks in database).
- **SC-002**: Queries processed end-to-end (retrieval + compression + answer generation) complete in ≤5 seconds (p95 latency) on a dataset of 10,000 product chunks for interactive use.
- **SC-003**: The confidence guard correctly prevents hallucinated answers on out-of-scope queries 100% of the time (zero hallucinations on queries with similarity < 0.7 and zero responses to zero-result edge cases).
- **SC-004**: Adaptive TopK correctly assigns the right retrieval count category (short/long/ambiguous) for at least 90% of test queries based on deterministic word-count and keyword-presence logic (>15 words AND no product name/action verb triggers ambiguous).
- **SC-005**: Context compression (dedup + score < 0.5 removal + >80% near-dup removal) reduces the number of tokens passed to the language model by at least 20% compared to passing all raw retrieved chunks, measured on the gold query set.
- **SC-006**: All RAG answers include valid citation metadata (ProductID + ChunkID) traceable back to the source database.
- **SC-007**: The evaluation CLI successfully runs the full gold dataset (≥10 Vietnamese queries) with 5-point Likert grading. Aggregate score is calculated as the mean rating across all queries; per-query scores and individual ratings are reported.
- **SC-008**: The gold dataset captures at least 10 distinct real-world Vietnamese product queries covering different query types (price, feature, availability, comparison).

---

## Assumptions

- Week 1 infrastructure is fully operational: PostgreSQL 17 with pgvector, async connection pool, and product/embeddings tables are in place.
- The embedding model selected for this environment (dev: local Ollama) will remain fixed for the full Week 2 cycle; no model switching mid-development.
- Vietnamese product data from the Week 1 seed script provides sufficient coverage for gold dataset creation.
- RRF constant k=60 is the baseline; tuning for specific datasets may occur post-Week 2.
- Near-duplicate detection uses string similarity heuristic (e.g., longest common subsequence ratio) with >80% threshold; exact algorithmic choice deferred to implementation.
- The evaluation CLI is designed for developer/QA use (terminal output), not end-user facing.
- Mixed-language (VN/EN) queries are processed with a single unified embedding model; no language-specific branching in Week 2.

---

## Design Constraints

- **Single-Database Principle**: All state (queries, embeddings, evaluation records, conversation history) lives in PostgreSQL 17. No auxiliary caches (Redis, in-memory stores) are introduced in Week 2.
- **Async-First**: All I/O operations (vector search, FTS queries, embedding calls, LLM calls) are non-blocking and properly awaited. No blocking calls in the event loop.
- **No Vendor Lock-in**: Embedding model and LLM routing is configuration-based (LiteLLM), not hard-coded. The system must support swapping models (Ollama ↔ Cloud APIs) without code changes.

---

## Security & Data Privacy

- **Sensitive Content in Chunks**: The system must handle product chunks that may contain pricing, inventory, or customer-specific information. No chunk text is logged to stdout; sensitive data is masked in debug logs and error messages.
- **Data Retention**: Retrieved chunks and evaluation records are stored indefinitely; no automatic deletion is required in Week 2. Manual cleanup is a future operational task.
- **Query Privacy**: User queries and their embeddings are persisted in the database for evaluation and analysis. This is acceptable for an internal SME system; external deployments must implement query anonymization policies separately.

---

## Mapping to Project Log (Week 2 Task Alignment)

| Spec Requirement | Week 2 Task | Notes |
|---|---|---|
| FR-001 (Embedding) | 2.1 (Local embed_text) | Text → vector conversion |
| FR-002 (Vector search) | 2.2 (Async vector search) | TopK result retrieval |
| FR-003 (Metadata extraction) | 2.3 (Metadata enrichment) | ≥5 keywords per chunk |
| FR-004 (Query normalization) | 2.4 (Query normalization) | Pydantic schema, no regex |
| FR-005 (Hybrid search + RRF) | 2.5 (Hybrid search) | RRF fusion with k=60 |
| FR-006 (Gold dataset) | 2.6 (Gold dataset) | ≥10 Vietnamese queries |
| FR-007 (RAG flow v1) | 2.7 (RAG Flow v1) | Rewrite → Search → Answer |
| FR-008 (Evaluation CLI + Likert scale) | 2.8 (Evaluation CLI Runner) | 5-point grading, aggregate score |
| FR-009 (Adaptive TopK) | 2.9 (Adaptive TopK) | Dynamic chunk count with deterministic logic |
| FR-010 (Similarity gap scoring) | 2.10 (Similarity gap scoring) | Score in state for observability |
| FR-011 (Citation metadata) | 2.11 (Citation metadata mapping) | ProductID + ChunkID per answer |
| FR-012 (Context compression) | 2.12 (Context compression) | Dedup + score < 0.5 + >80% near-dup removal |
| FR-013 (Confidence guard) | 2.13 (Confidence Threshold Guard) | similarity < 0.7 → decline |
| FR-014 (Embedding governance) | 2.2–2.3 (Embedding steps) | Model name + dimension stored |
| FR-015 (Query classification) | 2.9 (Adaptive TopK) | Deterministic hybrid heuristic |
| FR-016 (Edge case handling) | Cross-cutting | All 5 edge cases with explicit error handling |
