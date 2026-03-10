"""Retrieval node for LangGraph sales agent (T046).

Why: Calls make_retrieval_tool() (tools.py) to search knowledge base without LLM generation.

What: Finds chunks, citations, similarity scores, and cached answers (if any).
Populates state for confidence_node and answer_node downstream.
Does NOT call LLM — answer generation happens in answer_node only.

Enhancements (2026):
- SC09 fix: Expand short/pronoun queries using previous turn's citations
- SC10 fix: COMPARISON intent → split by "và/vs" and merge sub-query results
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from core.agent.tools import make_retrieval_tool

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from core.agent.state import AgentState

logger = logging.getLogger(__name__)

# Vietnamese pronouns that indicate cross-turn reference
_PRONOUN_RE = re.compile(
    r"\b(nó|cái\s+đó|sản\s+phẩm\s+đó|cái\s+này|cái\s+kia|mẫu\s+đó|mẫu\s+này|nó\s+có|nó\s+có\s+phù|nó\s+phù)\b",
    re.IGNORECASE | re.UNICODE,
)

# COMPARISON query splitters
_COMPARISON_SPLIT_RE = re.compile(
    r"\s+(?:và|vs\.?|versus|với|hay|hoặc)\s+",
    re.IGNORECASE | re.UNICODE,
)


def _expand_pronoun_query(query: str, state: AgentState) -> str:
    """SC09 fix: expand short/pronoun queries using the last cited product.

    If query contains Vietnamese pronouns AND previous citations exist in state,
    prepend the first citation's product name so retrieval finds the right product.

    E.g.: "Nó có phù hợp để làm video editing không?" +
          citations=[Dell XPS 15] →
          "Dell XPS 15 có phù hợp để làm video editing không?"
    """
    if not _PRONOUN_RE.search(query):
        return query

    citations = state.get("citations") or []
    if not citations:
        return query

    first = citations[0]
    if isinstance(first, dict):
        product_name = first.get("name")
    else:
        product_name = getattr(first, "name", None)
    if not product_name:
        return query

    expanded = _PRONOUN_RE.sub(product_name, query)
    logger.info("Pronoun expansion: %r → %r", query, expanded)
    return expanded


def _build_result_dict(result, citations_cls) -> dict:
    """Build state-update dict from a RetrievalResult."""
    from core.agent.state import Citation

    retrieved_chunks = [
        {"product_id": c["product_id"], "chunk_id": c["chunk_id"], "text": c["source_text"]}
        for c in result.citations
    ]
    citations = []
    for c in result.citations:
        try:
            citations.append(Citation(**c))
        except Exception:
            pass
    return {
        "retrieved_chunks": retrieved_chunks,
        "citations": citations,
        "similarity_score": result.best_similarity,
        "declined": result.declined,
        "cached_answer": result.cached_answer,
        "canonical_query": result.canonical_query,
        "query_vector": result.query_vector,
    }


async def retrieval_node(state: AgentState, config: RunnableConfig) -> dict:
    """Call retrieval tool and populate state (T046).

    Uses make_retrieval_tool(db) (no LLM) instead of make_rag_tool to avoid:
    - Wasted LLM calls for queries declined by confidence_node (Layer 2)
    - Double LLM calls for accepted queries (answer_node handles generation)
    - Wasted LLM calls for cache hits (cached_answer used directly by answer_node)

    DB session is injected via config["configurable"]["db"].
    Tool call logic lives in core/agent/tools.py (make_retrieval_tool factory).

    Returns:
        State update dict with retrieved_chunks, citations, similarity_score,
        declined (Layer 1 only), cached_answer, canonical_query, query_vector
    """
    db = (config.get("configurable") or {}).get("db")
    if db is None:
        return {
            "error": "Database connection not available",
            "declined": True,
            "similarity_score": 0.0,
            "retrieved_chunks": [],
            "citations": [],
            "cached_answer": None,
            "canonical_query": None,
            "query_vector": None,
        }

    intent = state.get("intent")
    raw_query = state["user_message"]

    # SC09: expand pronoun queries with previous citation context
    query = _expand_pronoun_query(raw_query, state)

    retrieve = make_retrieval_tool(db)

    try:
        result = await retrieve.ainvoke({"query": query, "intent": intent})
    except Exception as e:
        return {
            "error": f"Retrieval failed: {e!s}",
            "declined": True,
            "similarity_score": 0.0,
            "retrieved_chunks": [],
            "citations": [],
            "cached_answer": None,
            "canonical_query": None,
            "query_vector": None,
        }

    # SC10: COMPARISON fallback — split query and merge sub-results when main fails
    if result.declined and intent == "COMPARISON":
        parts = _COMPARISON_SPLIT_RE.split(query)
        # Strip stop words left/right of the product names
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 2:
            logger.info("COMPARISON split fallback: %d sub-queries from %r", len(parts), query)
            merged_citations: list[dict] = []
            best_sim = 0.0
            last_result = result  # fallback if all sub-queries also fail

            from services.rag.pipeline import search_and_retrieve

            for part in parts:
                try:
                    sub = await search_and_retrieve(db, part, intent="INFO_QUERY")
                    if not sub.declined:
                        # Deduplicate by chunk_id
                        existing_ids = {c["chunk_id"] for c in merged_citations}
                        for c in sub.citations:
                            if c["chunk_id"] not in existing_ids:
                                merged_citations.append(c)
                                existing_ids.add(c["chunk_id"])
                        best_sim = max(best_sim, sub.best_similarity)
                        last_result = sub
                except Exception as exc:
                    logger.warning("COMPARISON sub-query failed for %r: %s", part, exc)

            if merged_citations:
                from core.agent.state import Citation

                retrieved_chunks = [
                    {
                        "product_id": c["product_id"],
                        "chunk_id": c["chunk_id"],
                        "text": c["source_text"],
                    }
                    for c in merged_citations
                ]
                citations = []
                for c in merged_citations:
                    try:
                        citations.append(Citation(**c))
                    except Exception:
                        pass
                return {
                    "retrieved_chunks": retrieved_chunks,
                    "citations": citations,
                    "similarity_score": best_sim,
                    "declined": False,
                    "cached_answer": None,
                    "canonical_query": last_result.canonical_query,
                    "query_vector": last_result.query_vector,
                }

    return _build_result_dict(result, None)
