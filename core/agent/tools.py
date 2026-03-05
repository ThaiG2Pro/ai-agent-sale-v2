"""
Why this exists: LangGraph async tool registry for the sales agent.
What it does: Wraps Week 2 RAG pipeline as a typed @tool and provides
inventory lookup stub. All I/O validated through Pydantic schemas (Article VI).
DB session injected via factory closure pattern (see data-model.md §7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from services.rag.pipeline import RetrievalResult


class RAGSearchInput(BaseModel):
    """Input schema for RAG search tool."""

    query: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(pattern=r"^[0-9a-f\-]{36}$")
    model: str = Field(default="economy-chat", pattern=r"^[a-z0-9\-]+$")

    model_config = ConfigDict(strict=True)


class RetrievalInput(BaseModel):
    """Input schema for retrieval tool (no LLM generation).

    Intent is injected from router_node — avoids a redundant normalize_query LLM call
    that was causing intent=OTHER mismatch and wasting ~1-2s latency.
    """

    query: str = Field(min_length=1, max_length=2000, description="User's product search query")
    intent: str | None = Field(
        default=None,
        description="Pre-classified intent from router_node (e.g. PRICING, INFO_QUERY)",
    )

    model_config = ConfigDict(strict=False)  # allow intent=None


class CitationItem(BaseModel):
    """Citation item in RAG search output."""

    product_id: str
    chunk_id: str
    sku: str
    name: str
    source_text: str

    model_config = ConfigDict(strict=True)


class RAGSearchOutput(BaseModel):
    """Output schema for RAG search tool."""

    answer: str
    declined: bool
    citations: list[CitationItem]
    similarity_score: float = Field(ge=0.0, le=1.0)
    confidence_score: float = Field(ge=0.0, le=1.0)
    model_used: str
    chunks_used: int

    model_config = ConfigDict(strict=True)

    @classmethod
    def from_rag_result(cls, rag_result) -> RAGSearchOutput:
        """Bridge from Week 2 RAGResult to Week 3 tool output.

        Accepts both RAGResult Pydantic model and dict (for tests/legacy callers).
        Maps Week 2 fields to Week 3 schema:
        - best_similarity → similarity_score
        - best_similarity → confidence_score (same value in Phase 4)
        - chunks_after_compression → chunks_used
        """
        # Normalise: accept both Pydantic model and plain dict
        if isinstance(rag_result, dict):
            citations_list = rag_result.get("citations", [])
            answer = rag_result.get("answer", "")
            declined = rag_result.get("declined", False)
            best_similarity = rag_result.get("best_similarity", 0.0)
            model_used = rag_result.get("model_used", "")
            chunks_used = rag_result.get("chunks_after_compression", 0)
        else:
            citations_list = rag_result.citations or []
            answer = rag_result.answer
            declined = rag_result.declined
            best_similarity = rag_result.best_similarity
            model_used = rag_result.model_used
            chunks_used = rag_result.chunks_after_compression

        # Ensure citations have source_text field
        citations_with_text = []
        for citation in citations_list:
            if isinstance(citation, dict):
                if "source_text" not in citation:
                    citation = {**citation, "source_text": citation.get("name", "")}
                citations_with_text.append(citation)
            else:
                # Pydantic model
                d = citation.model_dump() if hasattr(citation, "model_dump") else vars(citation)
                if "source_text" not in d:
                    d["source_text"] = d.get("name", "")
                citations_with_text.append(d)

        return cls(
            answer=answer,
            declined=declined,
            citations=[CitationItem(**c) for c in citations_with_text],
            similarity_score=best_similarity,
            confidence_score=best_similarity,  # Phase 4: same as similarity
            model_used=model_used,
            chunks_used=chunks_used,
        )


class InventoryLookupInput(BaseModel):
    """Input schema for inventory lookup tool."""

    sku: str = Field(min_length=1, max_length=50, pattern=r"^[A-Z0-9_\-]+$")
    warehouse_id: str | None = Field(default=None, pattern=r"^[A-Z0-9]{3,10}$")

    model_config = ConfigDict(strict=True)


class InventoryLookupOutput(BaseModel):
    """Output schema for inventory lookup tool."""

    sku: str
    stock_level: int = Field(ge=0)
    warehouse_id: str | None = None
    available: bool
    error: str | None = None

    model_config = ConfigDict(strict=True)


# ── Tool Factories ────────────────────────────────────────────────────────


def make_rag_tool(db: AsyncSession):
    """Factory for RAG search tool with DB session closure.

    Wraps Week 2 answer_with_rag() pipeline and bridges RAGResult → RAGSearchOutput.
    Called by graph.py during initialization.
    """
    from services.rag import answer_with_rag

    @tool
    async def rag_search(input: RAGSearchInput) -> RAGSearchOutput:
        """Search product knowledge base and return cited answer.

        Args:
            input: RAGSearchInput with query, session_id, model

        Returns:
            RAGSearchOutput with answer, citations, and confidence scores
        """
        result = await answer_with_rag(db, input.query, input.model)
        return RAGSearchOutput.from_rag_result(result)

    return rag_search


def make_retrieval_tool(db):
    """Factory for retrieval tool (no LLM) with DB session closure.

    Wraps search_and_retrieve() — pipeline steps 1-11 only (no LLM generation).
    Returns a @tool so LLM can decide when to call it (or node can invoke directly).

    Returns:
        LangChain @tool: retrieve(input: RetrievalInput) -> RetrievalResult
    """
    from services.rag.pipeline import search_and_retrieve

    @tool(args_schema=RetrievalInput)
    async def retrieve(query: str, intent: str | None = None) -> RetrievalResult:
        """Search product knowledge base and return chunks without LLM generation.

        Use this to retrieve relevant product information, citations, and
        similarity scores before generating an answer.

        Args:
            query: User's product search query
            intent: Pre-classified intent from router_node (avoids redundant LLM call)

        Returns:
            RetrievalResult with chunks, citations, similarity scores, and
            optional cached_answer if L1/L2 cache hit.
        """
        return await search_and_retrieve(db, query, intent=intent)

    return retrieve


@tool
async def inventory_lookup(input: InventoryLookupInput) -> InventoryLookupOutput:
    """Look up product stock levels (Week 3 stub).

    Args:
        input: InventoryLookupInput with SKU and optional warehouse

    Returns:
        InventoryLookupOutput with stock level and availability

    Note:
        Real ERP integration deferred to Week 6.
    """
    return InventoryLookupOutput(
        sku=input.sku,
        stock_level=99,
        warehouse_id=input.warehouse_id,
        available=True,
        error=None,
    )
