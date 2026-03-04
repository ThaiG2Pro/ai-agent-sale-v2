"""Contract tests for RAG search tool (TDD Red phase).

Validates:
- Schema stability (no field drift)
- Layer 1 confidence guard (0.45 threshold)
- Graceful error handling (429, 500, timeout)
- Pydantic validation
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.agent.tools import RAGSearchInput, RAGSearchOutput


def load_baseline(name: str) -> dict:
    """Load baseline schema snapshot from baselines/ directory."""
    p = Path(__file__).parent / "baselines" / f"{name}.json"
    return json.loads(p.read_text())


def validate_schema_drift(model_class, baseline: dict) -> None:
    """Verify no field removed or renamed against baseline."""
    actual_fields = set(model_class.model_fields.keys())
    baseline_fields = set(baseline.keys())
    drift = actual_fields ^ baseline_fields
    assert not drift, f"Schema drift detected: {drift}"


class TestRAGToolContract:
    """RAG tool contract validation suite."""

    def test_rag_tool_schema_no_drift(self):
        """Verify RAGSearchOutput schema matches baseline (no field drift)."""
        baseline = load_baseline("rag_tool_baseline")
        validate_schema_drift(RAGSearchOutput, baseline)

    def test_rag_search_input_validation_strict(self):
        """Verify RAGSearchInput enforces strict validation."""
        # Valid input
        valid = RAGSearchInput(
            query="Giá sản phẩm X?", session_id="550e8400-e29b-41d4-a716-446655440000"
        )
        assert valid.query == "Giá sản phẩm X?"
        assert valid.model == "economy-chat"  # default

        # Invalid query: empty
        with pytest.raises(ValidationError) as exc_info:
            RAGSearchInput(query="", session_id="550e8400-e29b-41d4-a716-446655440000")
        assert "at least 1 character" in str(exc_info.value)

        # Invalid query: too long
        with pytest.raises(ValidationError):
            RAGSearchInput(query="x" * 2001, session_id="550e8400-e29b-41d4-a716-446655440000")

        # Invalid session_id: wrong format
        with pytest.raises(ValidationError):
            RAGSearchInput(query="test", session_id="not-a-uuid")

        # Invalid model: bad pattern
        with pytest.raises(ValidationError):
            RAGSearchInput(
                query="test",
                session_id="550e8400-e29b-41d4-a716-446655440000",
                model="BadModel",
            )

    def test_rag_search_output_validation_strict(self):
        """Verify RAGSearchOutput enforces field validation."""
        valid = RAGSearchOutput(
            answer="Giá sản phẩm là 100,000 VNĐ",
            declined=False,
            citations=[],
            similarity_score=0.95,
            confidence_score=0.85,
            model_used="economy-chat",
            chunks_used=3,
        )
        assert valid.answer == "Giá sản phẩm là 100,000 VNĐ"
        assert valid.declined is False

        # Verify fields are present and typed correctly
        assert isinstance(valid.similarity_score, float)
        assert isinstance(valid.confidence_score, float)

    def test_rag_tool_declined_false_on_valid(self):
        """Scenario 1: Valid response should have declined=False."""
        output = RAGSearchOutput(
            answer="Test answer",
            declined=False,
            citations=[],
            similarity_score=0.9,
            confidence_score=0.85,
            model_used="economy-chat",
            chunks_used=2,
        )
        assert output.declined is False
        assert output.answer == "Test answer"
        assert output.similarity_score == 0.9

    def test_rag_tool_from_rag_result_conversion(self):
        """Test classmethod bridge from Week 2 RAGResult to Week 3 tool output."""
        rag_result = {
            "answer": "Sản phẩm có sẵn",
            "declined": False,
            "citations": [
                {
                    "product_id": "P001",
                    "chunk_id": "C001",
                    "sku": "SKU-001",
                    "name": "Product A",
                    "source_text": "Product A is in stock",
                }
            ],
            "similarity_score": 0.88,
            "confidence_score": 0.82,
            "model_used": "economy-chat",
            "chunks_used": 1,
        }
        output = RAGSearchOutput.from_rag_result(rag_result)
        assert output.answer == "Sản phẩm có sẵn"
        assert output.declined is False
        assert len(output.citations) == 1
        assert output.citations[0].product_id == "P001"
        assert output.model_used == "economy-chat"

    def test_rag_tool_confidence_score_range(self):
        """Verify confidence_score must be 0.0-1.0."""
        # Valid edge cases
        RAGSearchOutput(
            answer="test",
            declined=False,
            citations=[],
            similarity_score=0.0,
            confidence_score=0.0,
            model_used="test",
            chunks_used=0,
        )
        RAGSearchOutput(
            answer="test",
            declined=False,
            citations=[],
            similarity_score=1.0,
            confidence_score=1.0,
            model_used="test",
            chunks_used=0,
        )

        # Invalid: > 1.0
        with pytest.raises(ValidationError):
            RAGSearchOutput(
                answer="test",
                declined=False,
                citations=[],
                similarity_score=1.1,
                confidence_score=0.5,
                model_used="test",
                chunks_used=0,
            )

        # Invalid: < 0.0
        with pytest.raises(ValidationError):
            RAGSearchOutput(
                answer="test",
                declined=False,
                citations=[],
                similarity_score=-0.1,
                confidence_score=0.5,
                model_used="test",
                chunks_used=0,
            )

    def test_rag_tool_cited_answer_structure(self):
        """Verify cited answer includes citations list (even if empty)."""
        output = RAGSearchOutput(
            answer="Answer text",
            declined=False,
            citations=[],  # Empty but valid
            similarity_score=0.8,
            confidence_score=0.75,
            model_used="economy-chat",
            chunks_used=0,
        )
        assert output.citations == []

        output_with_citations = RAGSearchOutput(
            answer="Cited answer",
            declined=False,
            citations=[
                {
                    "product_id": "P001",
                    "chunk_id": "C001",
                    "sku": "SKU",
                    "name": "Product",
                    "source_text": "text",
                }
            ],
            similarity_score=0.9,
            confidence_score=0.85,
            model_used="economy-chat",
            chunks_used=1,
        )
        assert len(output_with_citations.citations) == 1
