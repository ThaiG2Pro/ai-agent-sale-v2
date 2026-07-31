"""WP-V2-4 unit tests — episodic memory layer.

Covers the time-reference detector, event recording (products from citations,
kill switch, never-raises), recency retrieval with strict customer scoping,
memory_retrieval_node wiring, and the answer_node write hook.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.memory.episodic import (
    EpisodicMemoryService,
    format_event_line,
    has_time_reference,
)


def _event(**overrides):
    defaults = {
        "id": "e1",
        "customer_id": "cust_1",
        "thread_id": "t1",
        "user_message": "Laptop nào phù hợp lập trình?",
        "response_summary": "Đã tư vấn Dell XPS 15",
        "intent": "INFO_QUERY",
        "products": [{"name": "Dell XPS 15", "sku": "DELL-XPS15"}],
        "created_at": datetime(2026, 7, 30, 14, 30, tzinfo=UTC),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestTimeReference:
    def test_vietnamese_time_markers_match(self):
        for query in (
            "cái máy hôm qua em tư vấn ấy còn hàng không",
            "lần trước anh hỏi con laptop nào ấy nhỉ",
            "sản phẩm bữa trước shop giới thiệu",
            "cái điện thoại tuần trước",
            "em đã tư vấn cho anh con nào",
        ):
            assert has_time_reference(query), query

    def test_plain_queries_do_not_match(self):
        for query in ("Giá Dell XPS 15", "laptop nào tốt cho sinh viên", ""):
            assert not has_time_reference(query), query
        assert not has_time_reference(None)


class TestRecordEvent:
    @pytest.mark.asyncio
    async def test_records_products_from_citations(self):
        db = AsyncMock()
        db.add = MagicMock()
        citations = [
            SimpleNamespace(name="Dell XPS 15", sku="DELL-XPS15"),
            {"name": "Mac Air M3", "sku": "MAC-AIR-M3"},
            {"name": "Dell XPS 15", "sku": "DELL-XPS15"},  # duplicate name → deduped
        ]

        await EpisodicMemoryService().record_event(
            customer_id="cust_1",
            thread_id="t1",
            user_message="so sánh dell với mac",
            response="Dell mạnh hơn về...",
            intent="COMPARISON",
            citations=citations,
            db=db,
        )

        db.add.assert_called_once()
        event = db.add.call_args[0][0]
        assert event.customer_id == "cust_1"
        assert event.products == [
            {"name": "Dell XPS 15", "sku": "DELL-XPS15"},
            {"name": "Mac Air M3", "sku": "MAC-AIR-M3"},
        ]
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_kill_switch_skips_write(self, monkeypatch):
        monkeypatch.setattr("services.memory.episodic.settings.EPISODIC_MEMORY_ENABLED", False)
        db = AsyncMock()
        db.add = MagicMock()

        await EpisodicMemoryService().record_event(
            customer_id="cust_1",
            thread_id="t1",
            user_message="q",
            response="a",
            intent=None,
            citations=None,
            db=db,
        )

        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_db_error_never_raises(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock(side_effect=RuntimeError("db down"))

        await EpisodicMemoryService().record_event(
            customer_id="cust_1",
            thread_id="t1",
            user_message="q",
            response="a",
            intent=None,
            citations=None,
            db=db,
        )  # must not raise
        db.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_missing_customer_skips(self):
        db = AsyncMock()
        db.add = MagicMock()
        await EpisodicMemoryService().record_event(
            customer_id="",
            thread_id="t1",
            user_message="q",
            response="a",
            intent=None,
            citations=None,
            db=db,
        )
        db.add.assert_not_called()


class TestFormatEventLine:
    def test_line_contains_time_products_and_answer(self):
        line = format_event_line(_event())
        assert "30/07/2026" in line
        assert "Dell XPS 15" in line
        assert "Laptop nào phù hợp lập trình?" in line
        assert "Đã tư vấn Dell XPS 15" in line


class TestMemoryRetrievalNodeWiring:
    def _semantic_empty(self):
        return patch(
            "services.memory.semantic_memory.SemanticMemoryService.retrieve",
            new=AsyncMock(return_value=[]),
        )

    @pytest.mark.asyncio
    async def test_time_reference_pulls_episodic_events(self):
        from core.agent.nodes.memory_retrieval import memory_retrieval_node

        state = {
            "customer_id": "cust_1",
            "intent": "INFO_QUERY",
            "user_message": "cái máy hôm qua em tư vấn ấy còn hàng không",
        }
        config = {"configurable": {"db": AsyncMock()}}

        with self._semantic_empty():
            with patch(
                "services.memory.episodic.EpisodicMemoryService.recent_events",
                new=AsyncMock(return_value=[_event()]),
            ):
                update = await memory_retrieval_node(state, config)

        assert len(update["memory_context"]) == 1
        entry = update["memory_context"][0]
        assert entry["source"] == "episodic"
        assert "Dell XPS 15" in entry["summary_text"]
        assert update["declined"] is False  # memory context overrides decline

    @pytest.mark.asyncio
    async def test_no_time_reference_skips_episodic(self):
        from core.agent.nodes.memory_retrieval import memory_retrieval_node

        state = {
            "customer_id": "cust_1",
            "intent": "INFO_QUERY",
            "user_message": "Giá Dell XPS 15",
        }
        config = {"configurable": {"db": AsyncMock()}}

        with self._semantic_empty():
            with patch(
                "services.memory.episodic.EpisodicMemoryService.recent_events",
                new=AsyncMock(return_value=[_event()]),
            ) as mock_recent:
                update = await memory_retrieval_node(state, config)

        mock_recent.assert_not_awaited()
        assert update["memory_context"] == []

    @pytest.mark.asyncio
    async def test_kill_switch_skips_episodic(self, monkeypatch):
        from core.agent.nodes.memory_retrieval import memory_retrieval_node

        monkeypatch.setattr("core.config.settings.EPISODIC_MEMORY_ENABLED", False)
        state = {
            "customer_id": "cust_1",
            "intent": "INFO_QUERY",
            "user_message": "cái máy hôm qua em tư vấn ấy",
        }
        config = {"configurable": {"db": AsyncMock()}}

        with self._semantic_empty():
            with patch(
                "services.memory.episodic.EpisodicMemoryService.recent_events",
                new=AsyncMock(return_value=[_event()]),
            ) as mock_recent:
                update = await memory_retrieval_node(state, config)

        mock_recent.assert_not_awaited()
        assert update["memory_context"] == []

    @pytest.mark.asyncio
    async def test_episodic_failure_keeps_semantic_path(self):
        from core.agent.nodes.memory_retrieval import memory_retrieval_node

        state = {
            "customer_id": "cust_1",
            "intent": "INFO_QUERY",
            "user_message": "cái máy hôm qua em tư vấn ấy",
        }
        config = {"configurable": {"db": AsyncMock()}}

        with self._semantic_empty():
            with patch(
                "services.memory.episodic.EpisodicMemoryService.recent_events",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ):
                update = await memory_retrieval_node(state, config)

        assert update["memory_context"] == []  # graceful, no exception


class TestAnswerNodeWriteHook:
    @pytest.mark.asyncio
    async def test_business_response_records_event(self):
        """Path 0 (order executed / support handoff) records an episodic event."""
        from core.agent.nodes.answer import answer_node

        state = {
            "session_id": "s1",
            "customer_id": "cust_1",
            "user_message": "đặt 1 con chuột",
            "intent": "ORDER_PLACEMENT",
            "response": "Đơn hàng đã được tạo",
            "citations": [],
        }
        db = AsyncMock()
        db.add = MagicMock()

        await answer_node(state, {"configurable": {"db": db}})

        assert db.add.called  # episodic event appended
        event = db.add.call_args[0][0]
        assert event.customer_id == "cust_1"
        assert event.intent == "ORDER_PLACEMENT"

    @pytest.mark.asyncio
    async def test_declined_turn_records_nothing(self):
        from core.agent.nodes.answer import answer_node

        state = {
            "session_id": "s1",
            "customer_id": "cust_1",
            "user_message": "hàng ngoài danh mục",
            "intent": "PRICING",
            "declined": True,
            "citations": [],
        }
        db = AsyncMock()
        db.add = MagicMock()

        await answer_node(state, {"configurable": {"db": db}})

        db.add.assert_not_called()
