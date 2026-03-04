"""Retrieval node for LangGraph sales agent (T046).

Why: Calls RAG tool to search knowledge base and retrieve citations.

What: Finds rag_search tool from tools list, invokes with RAGSearchInput,
and populates state with retrieved_chunks, citations, similarity_score,
and propagates declined flag from Layer 1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.agent.tools import RAGSearchInput, make_rag_tool

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from core.agent.state import AgentState


async def retrieval_node(state: AgentState, config: RunnableConfig) -> dict:
    """Call RAG search tool and populate state with results (T046).

    DB session is injected via config["configurable"]["db"] — the caller must
    set this in the graph config:
        config = {"configurable": {"thread_id": ..., "db": db_session}}

    Returns:
        State update dict with retrieved_chunks, citations, similarity_score, declined
    """
    db = (config.get("configurable") or {}).get("db")
    if db is None:
        return {
            "error": "Database connection not available",
            "declined": True,
            "similarity_score": 0.0,
            "retrieved_chunks": [],
            "citations": [],
        }

    # Create RAG tool with DB session
    rag_tool = make_rag_tool(db)

    # Build input for RAG tool
    rag_input = RAGSearchInput(
        query=state["user_message"],
        session_id=state["session_id"],
        model=state.get("model_used") or "economy-chat",
    )

    # Call RAG tool — LangChain @tool with param named "input" requires wrapping
    try:
        rag_result = await rag_tool.ainvoke({"input": rag_input.model_dump()})
    except Exception as e:
        return {
            "error": f"RAG tool failed: {e!s}",
            "declined": True,
            "similarity_score": 0.0,
            "retrieved_chunks": [],
            "citations": [],
        }

    # Populate state from result
    # Layer 1 fast-path: propagate declined flag
    return {
        "retrieved_chunks": [
            {"product_id": c.product_id, "chunk_id": c.chunk_id, "text": c.source_text}
            for c in rag_result.citations
        ],
        "citations": [c for c in rag_result.citations],
        "similarity_score": rag_result.similarity_score,
        "declined": rag_result.declined,  # Layer 1 fast-path propagation
    }
