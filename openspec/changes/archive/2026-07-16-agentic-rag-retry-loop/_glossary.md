# Glossary — agentic-rag-retry-loop

| Term | Definition | Phase |
|------|------------|-------|
| Agentic RAG retry loop | Bounded self-evaluate → query-rewrite → re-retrieve loop wrapping `search_and_retrieve`; recovers poor first-pass retrievals instead of declining. | S1 |
| Retrieval sufficiency | Judgment that a retrieval can answer the query, decided by REUSING `best_similarity` / `chunks_after` / `confidence_score` — not a new scorer. | S2 |
| Query rewrite | A light-tier LLM re-phrasing of the user query that MUST preserve the original intent/product entity, used only as a new retrieval query. | S2 |
| Retry budget | Hard cap on rewrite+retry attempts via `RAG_RETRY_MAX_ATTEMPTS` (0..2, default 1); 0 = loop disabled (kill switch). | S2 |
| No-progress guard | Loop-termination when a rewrite yields an identical query, identical top `chunk_id`s, or no `best_similarity` improvement. | S2 |
| Grader/rewriter | The light-tier model call (`economy-chat` → `LIGHT_CHAT_MODEL`) performing the rewrite; never a premium/paid tier by default. | S2 |
| COMPARISON split | Pre-existing manual retry in `retrieval_node` (regex split on và/vs/với → 2 searches merged); coordinated with (mutually exclusive to) the general loop. | S1 |
| `retrieve_with_retry` | New shared pipeline helper (services layer) that wraps `search_and_retrieve` with the bounded retry loop; the single home of the loop, called by `retrieval_node`, `queue_consumer`, and `answer_with_rag`. | S3 |
| Retry-eligible insufficiency | The one decline reason that triggers a retry: `RetrievalResult.declined==True` AND `query_vector` populated (Layer-1 guard) — distinct from spam/embed-down declines which have an empty `query_vector`. | S3 |
| `RewrittenQuery` | Pydantic structured-output model returned by `AIGateway.rewrite_query`: `{query: str, keeps_subject: bool}`; `keeps_subject=false` → rewrite discarded. | S3 |
| `_write_retry_trace` | New helper writing per-attempt observability (attempt#, rewritten query, guard decision) into `model_traces.metadata_` JSON — no new DDL. | S4 |
| Abort-to-best-seen | Termination path when the rewriter or a re-`search_and_retrieve` raises mid-loop: the loop stops and returns the best (highest `best_similarity`) `RetrievalResult` seen so far instead of propagating the exception. | S4 |
