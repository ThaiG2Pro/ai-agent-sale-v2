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


class RAGSearchInput(BaseModel):
    """Input schema for RAG search tool."""

    query: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(pattern=r"^[0-9a-f\-]{36}$")
    model: str = Field(default="economy-chat", pattern=r"^[a-z0-9\-]+$")

    model_config = ConfigDict(strict=True)


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
    def from_rag_result(cls, rag_result: dict) -> RAGSearchOutput:
        """Bridge from Week 2 RAGResult to Week 3 tool output.

        Maps Week 2 fields to Week 3 schema:
        - best_similarity → similarity_score
        - best_similarity → confidence_score (same value in Phase 4)
        - chunks_after_compression → chunks_used
        """
        citations_list = rag_result.get("citations", [])
        # Ensure citations have source_text field
        citations_with_text = []
        for citation in citations_list:
            if isinstance(citation, dict):
                # If missing source_text, use name as fallback
                if "source_text" not in citation:
                    citation = {**citation, "source_text": citation.get("name", "")}
                citations_with_text.append(citation)

        return cls(
            answer=rag_result.get("answer", ""),
            declined=rag_result.get("declined", False),
            citations=[CitationItem(**c) for c in citations_with_text],
            similarity_score=rag_result.get("best_similarity", 0.0),
            confidence_score=rag_result.get(
                "best_similarity", 0.0
            ),  # Phase 4: same as similarity
            model_used=rag_result.get("model_used", ""),
            chunks_used=rag_result.get("chunks_after_compression", 0),
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
