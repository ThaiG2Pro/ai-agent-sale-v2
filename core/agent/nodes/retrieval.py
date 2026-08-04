"""Retrieval node for LangGraph sales agent (T046).

Why: Calls make_retrieval_tool() (tools.py) to search knowledge base without LLM generation.

What: Finds chunks, citations, similarity scores, and cached answers (if any).
Populates state for confidence_node and answer_node downstream.
Does NOT call LLM — answer generation happens in answer_node only.

Enhancements (2026):
- SC09 fix: Expand short/pronoun queries using previous turn's citations
- SC10 fix: COMPARISON intent → split by "và/vs" and merge sub-query results
- WP-V2-3: clarify-reply merge (awaiting_clarification turn) + LLM query
  decomposition for declined multi-intent queries (regex split kept as fallback)
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from core.agent.tools import make_retrieval_tool
from core.config import settings

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from core.agent.state import AgentState

logger = logging.getLogger(__name__)

# Vietnamese pronouns that indicate cross-turn reference
_PRONOUN_RE = re.compile(
    r"\b(nó|cái\s+đó|sản\s+phẩm\s+đó|cái\s+này|cái\s+kia|mẫu\s+đó|mẫu\s+này|nó\s+có|nó\s+có\s+phù|nó\s+phù)\b",
    re.IGNORECASE | re.UNICODE,
)

# Ordinal / Index references to previous citations (e.g. '1', '2', 'lap 1', 'mẫu 1', 'số 2')
_ORDINAL_RE = re.compile(
    r"\b(?:lap|mẫu|sản\s+phẩm|cái|số)\s*([1-9])\b|\b([1-9])\b",
    re.IGNORECASE | re.UNICODE,
)

# COMPARISON query splitters
_COMPARISON_SPLIT_RE = re.compile(
    r"\s+(?:và|vs\.?|versus|với|hay|hoặc)\s+",
    re.IGNORECASE | re.UNICODE,
)

# WP-V2-3: intents whose declined queries are worth an LLM decomposition pass
# (multi-intent like "giá X và còn hàng không", not just COMPARISON)
_DECOMPOSABLE_INTENTS = {"COMPARISON", "INFO_QUERY", "PRICING", "AVAILABILITY"}

# Decomposition hard cap — more sub-queries than this is LLM noise, not intent
_MAX_SUB_QUERIES = 3


async def _decompose_query(query: str) -> list[str] | None:
    """WP-V2-3: LLM query decomposition (economy model, Pydantic-validated).

    Returns 2..3 self-contained sub-queries, or None when the LLM call fails /
    returns malformed output / finds nothing to split (single-intent query).
    Callers fall back to the COMPARISON regex split on None.
    """
    from core.agent.state import DecomposedQuery
    from services.ai import AIGateway

    system_prompt = (
        "You split a Vietnamese/English e-commerce customer query into independent "
        "sub-queries. Each sub-query must be self-contained (keep the full product "
        "name in every sub-query it concerns) and answerable alone against a product "
        "catalog. Split ONLY genuinely separate intents or separate products "
        "(e.g. 'Giá Galaxy A55 và còn hàng không?' → "
        "['Giá Galaxy A55', 'Galaxy A55 còn hàng không']). "
        "If the query is a single intent about a single product, return it as the "
        "only sub-query. Return at most 3 sub-queries. "
        "Respond ONLY with valid JSON matching the schema."
    )
    try:
        result = await AIGateway.complete(
            model="economy-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ],
            response_format=DecomposedQuery,
        )
        decomposed = DecomposedQuery.model_validate_json(result.choices[0].message.content)
    except Exception as e:
        logger.warning("Query decomposition LLM call failed for %r: %s", query, e)
        return None

    parts = [q.strip() for q in decomposed.sub_queries[:_MAX_SUB_QUERIES] if q and q.strip()]
    if len(parts) < 2:
        return None
    return parts


def _get_citation_name(citations: list, index: int) -> str | None:
    """Safely extract product name from citation at 0-based index."""
    if not citations or index < 0 or index >= len(citations):
        return None
    item = citations[index]
    if isinstance(item, dict):
        return item.get("name")
    return getattr(item, "name", None)


def _expand_pronoun_query(query: str, state: AgentState) -> str:
    """SC09 & Ordinal index fix: expand pronouns and ordinal references using citations in state.

    E.g. 1: "Nó có phù hợp không?" + citations=[Dell XPS 15] → "Dell XPS 15 có phù hợp không?"
    E.g. 2: "so sánh 1 và 2" + citations=[ASUS VivoBook, Lenovo ThinkPad] →
            "so sánh ASUS VivoBook và Lenovo ThinkPad"
    E.g. 3: "lap 1" + citations=[ASUS VivoBook] → "ASUS VivoBook"
    """
    citations = state.get("citations") or []
    if not citations:
        return query

    # 1. Expand ordinal / index references (e.g., '1', '2', 'lap 1', 'mẫu 1', 'số 2')
    def replace_ordinal(match: re.Match) -> str:
        num_str = match.group(1) or match.group(2)
        if not num_str:
            return match.group(0)
        num = int(num_str)
        citation_name = _get_citation_name(citations, num - 1)
        if citation_name:
            return citation_name
        return match.group(0)

    if _ORDINAL_RE.search(query):
        new_query = _ORDINAL_RE.sub(replace_ordinal, query)
        if new_query != query:
            logger.info("Ordinal expansion: %r → %r", query, new_query)
            return new_query

    # 2. Expand general pronouns (e.g. 'nó', 'cái đó', 'mẫu này') to citations[0]
    if _PRONOUN_RE.search(query):
        first_name = _get_citation_name(citations, 0)
        if first_name:
            expanded = _PRONOUN_RE.sub(first_name, query)
            logger.info("Pronoun expansion: %r → %r", query, expanded)
            return expanded

    return query


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

    # WP-V2-3: clarify-reply turn — merge the customer's reply into the stored
    # original query and clear the pending flag. clarify_count stays at 1 so a
    # still-borderline merged query declines instead of clarifying again.
    clarify_updates: dict = {}
    if state.get("awaiting_clarification") and state.get("clarify_original_query"):
        raw_query = f"{state['clarify_original_query']} {raw_query}"
        clarify_updates = {"awaiting_clarification": False, "clarify_original_query": None}
        logger.info("Clarify merge → %r", raw_query)
    elif int(state.get("clarify_count") or 0):
        # Fresh query (no pending clarify) → the 1-clarify budget resets
        clarify_updates = {"clarify_count": 0}

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
            **clarify_updates,
        }

    # WP-V2-3 / SC10: declined multi-intent or comparison query → decompose with
    # the LLM (kill switch: QUERY_DECOMPOSITION_ENABLED), search each sub-query
    # and merge. The old COMPARISON regex split remains the fallback when the
    # LLM call fails or the switch is off.
    if result.declined and intent in _DECOMPOSABLE_INTENTS:
        parts: list[str] | None = None
        if settings.QUERY_DECOMPOSITION_ENABLED:
            parts = await _decompose_query(query)
            if parts:
                logger.info("LLM decomposition: %d sub-queries from %r", len(parts), query)
        if parts is None and intent == "COMPARISON":
            regex_parts = [p.strip() for p in _COMPARISON_SPLIT_RE.split(query) if p.strip()]
            if len(regex_parts) >= 2:
                logger.info(
                    "COMPARISON split fallback: %d sub-queries from %r", len(regex_parts), query
                )
                parts = regex_parts
        if parts:
            merged = await _merge_subquery_results(db, parts)
            if merged is not None:
                return {**merged, **clarify_updates}

    return {**_build_result_dict(result, None), **clarify_updates}


async def _merge_subquery_results(db, parts: list[str]) -> dict | None:
    """Search each sub-query and merge citations (dedup by chunk_id).

    Returns a state-update dict, or None when every sub-query also declined
    (caller falls through to the original declined result).
    """
    from services.rag.pipeline import search_and_retrieve

    merged_citations: list[dict] = []
    best_sim = 0.0
    last_result = None

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
            logger.warning("Sub-query retrieval failed for %r: %s", part, exc)

    if not merged_citations or last_result is None:
        return None

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
