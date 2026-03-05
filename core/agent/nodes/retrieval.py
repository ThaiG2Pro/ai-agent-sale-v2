"""Retrieval node for LangGraph sales agent (T046).

Why: Calls make_retrieval_tool() (tools.py) to search knowledge base without LLM generation.

What: Finds chunks, citations, similarity scores, and cached answers (if any).
Populates state for confidence_node and answer_node downstream.
Does NOT call LLM — answer generation happens in answer_node only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.agent.tools import make_retrieval_tool

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from core.agent.state import AgentState


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

    try:
        retrieve = make_retrieval_tool(db)
        result = await retrieve.ainvoke(
            {
                "query": state["user_message"],
                "intent": state.get("intent"),
            }
        )
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

    # Build retrieved_chunks from citations (used by answer_node for context)
    retrieved_chunks = [
        {"product_id": c["product_id"], "chunk_id": c["chunk_id"], "text": c["source_text"]}
        for c in result.citations
    ]

    # Convert citations dicts to Citation Pydantic objects
    from core.agent.state import Citation

    citations = []
    for c in result.citations:
        try:
            citations.append(Citation(**c))
        except Exception:
            pass  # skip malformed citations

    return {
        "retrieved_chunks": retrieved_chunks,
        "citations": citations,
        "similarity_score": result.best_similarity,
        "declined": result.declined,  # Layer 1 fast-path only
        "cached_answer": result.cached_answer,
        "canonical_query": result.canonical_query,
        "query_vector": result.query_vector,
    }
