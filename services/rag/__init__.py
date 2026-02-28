"""RAG pipeline for Vietnamese SME AI Sales Agent.

Why this exists: Modular RAG orchestration with clean separation of concerns.
What it does: Exposes public API (answer_with_rag, search_products, ingest_product_text)
             while organizing implementation into focused modules.
"""

from services.rag.compression import _overlap_ratio, compress_context
from services.rag.constants import DECLINE_MESSAGE
from services.rag.ingest import ingest_product_text
from services.rag.pipeline import RAGResult, answer_with_rag
from services.rag.query import classify_query, compute_adaptive_topk
from services.rag.retrieval import hybrid_search_rrf, search_products

__all__ = [
    "DECLINE_MESSAGE",
    "RAGResult",
    "_overlap_ratio",
    "answer_with_rag",
    "classify_query",
    "compress_context",
    "compute_adaptive_topk",
    "hybrid_search_rrf",
    "ingest_product_text",
    "search_products",
]
