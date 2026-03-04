"""
Why this exists: LangGraph async tool registry for the sales agent.
What it does: Wraps Week 2 RAG pipeline as a typed @tool and provides
inventory lookup stub. All I/O validated through Pydantic schemas (Article VI).
DB session injected via factory closure pattern (see data-model.md §7).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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
        """Bridge from Week 2 RAGResult to Week 3 tool output."""
        return cls(
            answer=rag_result.get("answer", ""),
            declined=rag_result.get("declined", False),
            citations=[
                CitationItem(**citation) for citation in rag_result.get("citations", [])
            ],
            similarity_score=rag_result.get("similarity_score", 0.0),
            confidence_score=rag_result.get("confidence_score", 0.0),
            model_used=rag_result.get("model_used", ""),
            chunks_used=rag_result.get("chunks_used", 0),
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
