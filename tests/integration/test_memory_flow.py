"""Integration tests for full memory flow (Week 5, US2, T074).

Tests cover:
- Happy path: checkpoint save → restart → memory recalled
- Cold start: new customer with zero history
- Cross-session recall: semantic memory from past session
- Checkpoint durability: server restart preserves state
- Full pipeline: summarization → embedding → retrieval
- API flow: Intent extraction → status updates → filtering
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app
from core.config import settings
from services.database import get_db

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.integration
class TestMemoryFlow:
    """Integration test suite for complete memory workflow."""

    @pytest.mark.asyncio
    async def test_memory_flow_pricing_intent_end_to_end(
        self,
        async_session_test_db: AsyncSession,
        monkeypatch,
    ) -> None:
        """Test full flow: Create customer → Extract intent → GET via API.

        Scenario: Customer messages about pricing → system classifies as PRICING intent
        → high urgency → API query returns it with all fields populated.

        Steps:
            1. Manually insert IntentTracking row (simulating extraction)
            2. GET /memory/intent/{customer_id} should return the row
            3. Verify response has all expected fields (budget_range, urgency_level, etc.)
        """

        # Override DB dependency to use test DB
        async def get_db_override():
            return async_session_test_db

        app.dependency_overrides[get_db] = get_db_override
        monkeypatch.setattr(settings, "X_ADMIN_KEY", "test-admin-key-12345")

        # Step 1: Insert test records — IntentTracking holds lightweight state
        # (status/version), SalesIntentLog holds the extracted signal detail.
        # See api/routes/memory.py::_build_intent_response for how they join.
        customer_id = "test_customer_pricing_001"
        thread_id = f"{customer_id}_thread"
        from models.schema import IntentTracking, SalesIntentLog

        test_intent = IntentTracking(
            customer_id=customer_id,
            thread_id=thread_id,
            status="NEW",
            version=1,
            last_updated_by="agent",
        )
        test_log = SalesIntentLog(
            customer_id=customer_id,
            thread_id=thread_id,
            primary_intent="PRICING",
            urgency_level="HIGH",
            budget_range="50000-100000",
            product_interest=["Product A", "Product B"],
            decision_timeline="This week",
            contact_preference="Email",
            extraction_model="test-model",
        )
        async_session_test_db.add_all([test_intent, test_log])
        await async_session_test_db.commit()

        # Step 2: GET /memory/intent/{customer_id}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/memory/intent/{customer_id}")

        # Step 3: Verify response
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()

        assert data["customer_id"] == customer_id
        assert data["urgency_level"] == "HIGH"
        assert data["budget_range"] == "50000-100000"
        assert data["product_interest"] == ["Product A", "Product B"]
        assert data["decision_timeline"] == "This week"
        assert data["contact_preference"] == "Email"
        assert data["version"] == 1
        assert data["intent_status"] == "NEW"

        # Cleanup
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_intents_with_filtering(
        self,
        async_session_test_db: AsyncSession,
        monkeypatch,
    ) -> None:
        """Test GET /memory/intents with urgency_level filter.

        Scenario: Multiple intents in DB → filter by HIGH urgency → only HIGH returned.
        """

        async def get_db_override():
            return async_session_test_db

        app.dependency_overrides[get_db] = get_db_override
        monkeypatch.setattr(settings, "X_ADMIN_KEY", "test-admin-key-12345")

        # Insert test intents with different urgency levels (state in
        # IntentTracking, extracted signal detail in SalesIntentLog).
        from models.schema import IntentTracking, SalesIntentLog

        cases = [
            ("cust_high_001", "COMPLAINT", "HIGH", ["Service A"], "NEW"),
            ("cust_medium_001", "INFO_QUERY", "MEDIUM", ["Service B"], "NEW"),
            ("cust_high_002", "NEGOTIATION", "HIGH", ["Service C"], "ENGAGED"),
        ]
        rows: list[object] = []
        for customer_id, primary_intent, urgency, products, status in cases:
            thread_id = f"{customer_id}_thread"
            rows.append(
                IntentTracking(
                    customer_id=customer_id,
                    thread_id=thread_id,
                    status=status,
                    version=1,
                    last_updated_by="agent",
                )
            )
            rows.append(
                SalesIntentLog(
                    customer_id=customer_id,
                    thread_id=thread_id,
                    primary_intent=primary_intent,
                    urgency_level=urgency,
                    product_interest=products,
                    extraction_model="test-model",
                )
            )
        async_session_test_db.add_all(rows)
        await async_session_test_db.commit()

        # Query with admin key and urgency filter
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/memory/intents",
                params={"urgency_level": "HIGH"},
                headers={"X-Admin-Key": "test-admin-key-12345"},
            )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()

        assert data["total"] == 2, f"Expected 2 HIGH urgency intents, got {data['total']}"
        assert len(data["items"]) == 2
        assert all(item["urgency_level"] == "HIGH" for item in data["items"])

        # Cleanup
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_intents_without_admin_key_returns_401(
        self,
        async_session_test_db: AsyncSession,
    ) -> None:
        """Test GET /memory/intents without admin key returns 401.

        Scenario: Client doesn't provide X-Admin-Key → receives 401 Unauthorized.
        """

        async def get_db_override():
            return async_session_test_db

        app.dependency_overrides[get_db] = get_db_override

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/memory/intents")

        assert response.status_code == 401
        assert "Admin key required" in response.json()["detail"]

        # Cleanup
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_update_intent_status_with_optimistic_lock(
        self,
        async_session_test_db: AsyncSession,
        monkeypatch,
    ) -> None:
        """Test PATCH /memory/intent/{customer_id}/status with version check.

        Scenario: Update status with correct version → succeeds.
        """

        async def get_db_override():
            return async_session_test_db

        app.dependency_overrides[get_db] = get_db_override
        monkeypatch.setattr(settings, "X_ADMIN_KEY", "test-admin-key-12345")

        # Insert test intent
        from models.schema import IntentTracking

        customer_id = "cust_update_001"
        test_intent = IntentTracking(
            customer_id=customer_id,
            thread_id=f"{customer_id}_thread",
            status="NEW",
            version=1,
            last_updated_by="agent",
        )
        async_session_test_db.add(test_intent)
        await async_session_test_db.commit()

        # Update status via API
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                f"/memory/intent/{customer_id}/status",
                json={"new_status": "CONTACTED", "expected_version": 1},
                headers={"X-Admin-Key": "test-admin-key-12345"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["intent_status"] == "CONTACTED"
        assert data["version"] == 2  # Version incremented by optimistic lock

        # Cleanup
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_update_intent_status_version_conflict_returns_409(
        self,
        async_session_test_db: AsyncSession,
        monkeypatch,
    ) -> None:
        """Test PATCH with stale version returns 409 Conflict.

        Scenario: Client sends expected_version=1 but DB version is 2 → 409.
        """

        async def get_db_override():
            return async_session_test_db

        app.dependency_overrides[get_db] = get_db_override
        monkeypatch.setattr(settings, "X_ADMIN_KEY", "test-admin-key-12345")

        # Insert test intent
        from models.schema import IntentTracking

        customer_id = "cust_conflict_001"
        test_intent = IntentTracking(
            customer_id=customer_id,
            thread_id=f"{customer_id}_thread",
            status="NEW",
            version=2,  # Version is 2
            last_updated_by="agent",
        )
        async_session_test_db.add(test_intent)
        await async_session_test_db.commit()

        # Try to update with stale version
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch(
                f"/memory/intent/{customer_id}/status",
                json={"new_status": "CONTACTED", "expected_version": 1},  # Stale
                headers={"X-Admin-Key": "test-admin-key-12345"},
            )

        assert response.status_code == 409
        assert "Version conflict" in response.json()["detail"]

        # Cleanup
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_intent_unknown_customer_returns_404(
        self,
        async_session_test_db: AsyncSession,
    ) -> None:
        """Test GET /memory/intent/{customer_id} with unknown customer returns 404.

        Scenario: Query for non-existent customer_id → 404 Not Found.
        """

        async def get_db_override():
            return async_session_test_db

        app.dependency_overrides[get_db] = get_db_override

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/memory/intent/nonexistent_customer_12345")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

        # Cleanup
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_summary_created_at_threshold(self):
        """T111: Integration test - verify _maybe_summarize triggers at message threshold.

        Scenario:
        1. Create state with 22 messages (at THRESHOLD)
        2. Call _maybe_summarize
        3. Verify should_summarize returns True (trigger condition met)
        4. Verify LLM and DB calls are attempted (even if mocked)
        """
        from unittest.mock import AsyncMock, patch

        from core.agent.state import make_initial_state
        from services.memory.background import _maybe_summarize
        from services.memory.summarizer import ConversationSummarizer

        session_id = "test_summary_threshold_001"
        customer_id = "test_cust_threshold"

        # Setup: Create state with 22 messages
        state = make_initial_state(
            user_message="Test",
            session_id=session_id,
            customer_id=customer_id,
        )
        state["messages"] = [{"role": "user", "content": f"Message {i}"} for i in range(22)]
        state["thread_summary_exists"] = False

        # Mock db_factory
        def mock_db_factory():
            class CtxMgr:
                async def __aenter__(self):
                    db = AsyncMock()
                    db.execute = AsyncMock()
                    db.commit = AsyncMock()
                    return db

                async def __aexit__(self, *args):
                    pass

            return CtxMgr()

        # Verify should_summarize returns True at threshold
        assert ConversationSummarizer.should_summarize(
            message_count=22,
            has_existing_summary=False,
            messages_since_last_summary=22,
        ), "should_summarize must return True at 22-message threshold"

        # Mock LiteLLM to avoid actual API calls
        with patch(
            "services.memory.summarizer.AIGateway.complete",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_response = AsyncMock()
            mock_response.parsed = None  # Will trigger fallback
            mock_response.choices = [AsyncMock(message=AsyncMock(content="Summary text"))]
            mock_llm.return_value = mock_response

            # Execute: Call _maybe_summarize
            # It's OK if it fails due to mocking - we're testing the threshold logic
            await _maybe_summarize(
                customer_id=customer_id,
                thread_id=session_id,
                state=state,
                db_factory=mock_db_factory,
            )

            # Verify: crossing the threshold actually triggers summarization (LLM call)
            assert mock_llm.called, "summarization must be invoked once threshold is crossed"


# ---------------------------------------------------------------------------
# P0-1 / P0-5 regression tests — real DB, real compiled graph
# ---------------------------------------------------------------------------


def _seed_vector() -> list[float]:
    """Deterministic 1024-dim vector; identical query vector → cosine 1.0."""
    return [0.1] * settings.EMBED_DIMENSION


def _make_router_response(intent: str, confidence: float = 0.9):
    """Mock LiteLLM response for router_node (same shape as test_agent_flow)."""
    import json
    from unittest.mock import MagicMock

    content = json.dumps(
        {
            "primary_intent": intent,
            "secondary_intents": [],
            "confidence": confidence,
            "reasoning": "test reasoning",
        },
    )
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_answer_response(text: str = "Test answer"):
    from unittest.mock import MagicMock

    choice = MagicMock()
    choice.message.content = text
    response = MagicMock()
    response.choices = [choice]
    return response


def _mock_retrieval_tool(similarity_score: float = 0.9):
    """Factory (db) -> tool mock, matching make_retrieval_tool signature."""
    from unittest.mock import AsyncMock, MagicMock

    from services.rag.pipeline import RetrievalResult

    result = RetrievalResult(
        cached_answer=None,
        cached_citations=[],
        declined=False,
        citations=[],
        chunks=[],
        best_similarity=similarity_score,
        similarity_gap=0.0,
        canonical_query="test query",
        query_vector=_seed_vector(),
        query_category="INFO_QUERY",
        top_k_used=5,
    )
    mock_tool = MagicMock()
    mock_tool.ainvoke = AsyncMock(return_value=result)
    return lambda db: mock_tool


@pytest.mark.integration
@pytest.mark.asyncio
async def test_semantic_recall_through_real_compiled_graph(db_session) -> None:
    """P0-1 E2E: memory_context is NON-EMPTY when the REAL compiled graph runs.

    Lesson "test xanh nhưng feature gãy": unit tests that call the node
    directly can pass while the graph wiring is broken. This test seeds a
    semantic memory row in the real Postgres DB, builds the graph via
    build_graph() (the exact production registration at graph.py:82), and
    invokes it end-to-end with db injected via config["configurable"]["db"].
    Only the LLM/embedding boundaries are mocked.
    """
    from unittest.mock import AsyncMock, patch

    from langgraph.checkpoint.memory import MemorySaver
    from sqlalchemy import delete as sa_delete

    from core.agent.graph import build_graph
    from core.agent.state import make_initial_state
    from models.schema import ConversationSummary, EmbeddingStatus, SemanticMemory

    customer_id = "recall_e2e_cust"
    vec = _seed_vector()

    # conftest db_session does NOT clean these two tables — do it ourselves.
    async def _cleanup():
        await db_session.rollback()  # clear any failed in-flight transaction
        await db_session.execute(
            sa_delete(SemanticMemory).where(SemanticMemory.customer_id == customer_id)
        )
        await db_session.execute(
            sa_delete(ConversationSummary).where(ConversationSummary.customer_id == customer_id)
        )
        await db_session.commit()

    await _cleanup()
    try:
        # Seed: past-session summary + its ACTIVE embedding
        summary_row = ConversationSummary(
            customer_id=customer_id,
            thread_id="thread_past_001",
            summary_text="Customer previously asked about laptop pricing",
            summary_model="economy-chat",
        )
        db_session.add(summary_row)
        await db_session.commit()

        db_session.add(
            SemanticMemory(
                summary_id=summary_row.id,
                customer_id=customer_id,
                embedding=vec,
                embedding_model=settings.EMBED_MODEL,
                embedding_dimension=settings.EMBED_DIMENSION,
                status=EmbeddingStatus.ACTIVE,
            )
        )
        await db_session.commit()

        mock_llm = AsyncMock(
            side_effect=[
                _make_router_response("INFO_QUERY"),
                _make_answer_response("Here is the price."),
            ],
        )

        graph = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "thread_recall_e2e", "db": db_session}}
        state = make_initial_state(
            user_message="Laptop giá bao nhiêu?",
            session_id="thread_recall_e2e",
            customer_id=customer_id,
        )

        with (
            patch("services.ai.ai_router.acompletion", mock_llm),
            patch(
                "core.agent.nodes.retrieval.make_retrieval_tool",
                _mock_retrieval_tool(similarity_score=0.9),
            ),
            # Faithful shape: AIGateway.embed returns a BATCH (list[list[float]])
            patch(
                "services.ai.AIGateway.embed",
                new=AsyncMock(return_value=[vec]),
            ),
        ):
            result = await graph.ainvoke(state, config)

        assert result["memory_context"], (
            "P0-1 regression: semantic recall must NOT be empty in the real compiled graph"
        )
        assert (
            result["memory_context"][0]["summary_text"]
            == "Customer previously asked about laptop pricing"
        )
        assert result["memory_retrieval_scores"]
        assert result["memory_retrieval_scores"][0] >= 0.75
    finally:
        await _cleanup()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_summary_save_then_resummary_upserts(db_session) -> None:
    """P0-5: save #1 inserts, re-summary #2 for the same (customer, thread)
    UPDATES the row instead of violating uq_summary_customer_thread.

    Runs against the real Postgres constraint — the old code silently lost
    every re-summary (IntegrityError swallowed upstream).
    """
    from sqlalchemy import delete as sa_delete, select

    from core.agent.state import ConversationSummaryOutput
    from models.schema import ConversationSummary
    from services.memory.summarizer import ConversationSummarizer

    customer_id = "resummary_cust"
    thread_id = "thread_resummary_001"

    async def _cleanup():
        await db_session.execute(
            sa_delete(ConversationSummary).where(ConversationSummary.customer_id == customer_id)
        )
        await db_session.commit()

    await _cleanup()
    try:
        first = ConversationSummaryOutput(
            summary_text="First summary",
            products_discussed=["Laptop A"],
            open_questions=["Ship time?"],
            summary_model="economy-chat",
        )
        await ConversationSummarizer.save_summary(
            summary=first,
            session_id=thread_id,
            customer_id=customer_id,
            turn_count=20,
            db=db_session,
        )

        second = ConversationSummaryOutput(
            summary_text="Second summary after 10 more messages",
            products_discussed=["Laptop A", "Laptop B"],
            open_questions=[],
            customer_preference="prefers lightweight models",
            budget_stated="under 20M VND",
            summary_model="economy-chat",
        )
        await ConversationSummarizer.save_summary(
            summary=second,
            session_id=thread_id,
            customer_id=customer_id,
            turn_count=30,
            db=db_session,
        )

        rows = (
            (
                await db_session.execute(
                    select(ConversationSummary).where(
                        ConversationSummary.customer_id == customer_id,
                        ConversationSummary.thread_id == thread_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, "re-summary must upsert, not duplicate or vanish"
        row = rows[0]
        assert row.summary_text == "Second summary after 10 more messages"
        assert row.products_discussed == ["Laptop A", "Laptop B"]
        assert row.customer_preference == "prefers lightweight models"
        assert row.budget_stated == "under 20M VND"
        assert row.summary_version == 2, "upsert must bump summary_version"
    finally:
        await _cleanup()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_semantic_memory_retrieve_excludes_stale_rows(db_session) -> None:
    """T123: retrieve() excludes STALE embeddings against the real Postgres WHERE
    clause — the filter lives in raw SQL, so a mocked DB can't exercise it; only
    a real seeded STALE row proves exclusion actually happens.
    """
    from unittest.mock import AsyncMock, patch

    from sqlalchemy import delete as sa_delete

    from models.schema import ConversationSummary, EmbeddingStatus, SemanticMemory
    from services.memory.semantic_memory import SemanticMemoryService

    customer_id = "stale_exclusion_cust"
    vec = _seed_vector()

    async def _cleanup():
        await db_session.rollback()
        await db_session.execute(
            sa_delete(SemanticMemory).where(SemanticMemory.customer_id == customer_id)
        )
        await db_session.execute(
            sa_delete(ConversationSummary).where(ConversationSummary.customer_id == customer_id)
        )
        await db_session.commit()

    await _cleanup()
    try:
        active_summary = ConversationSummary(
            customer_id=customer_id,
            thread_id="thread_active",
            summary_text="Active summary",
            summary_model="economy-chat",
        )
        stale_summary = ConversationSummary(
            customer_id=customer_id,
            thread_id="thread_stale",
            summary_text="Stale summary (old embedding model)",
            summary_model="economy-chat",
        )
        db_session.add_all([active_summary, stale_summary])
        await db_session.commit()

        db_session.add_all(
            [
                SemanticMemory(
                    summary_id=active_summary.id,
                    customer_id=customer_id,
                    embedding=vec,
                    embedding_model=settings.EMBED_MODEL,
                    embedding_dimension=settings.EMBED_DIMENSION,
                    status=EmbeddingStatus.ACTIVE,
                ),
                SemanticMemory(
                    summary_id=stale_summary.id,
                    customer_id=customer_id,
                    embedding=vec,
                    embedding_model="old-model-v1",
                    embedding_dimension=settings.EMBED_DIMENSION,
                    status=EmbeddingStatus.STALE,
                ),
            ]
        )
        await db_session.commit()

        with patch("services.ai.AIGateway.embed", new=AsyncMock(return_value=[vec])):
            results = await SemanticMemoryService().retrieve(
                customer_id=customer_id, query="anything", db=db_session
            )

        assert len(results) == 1, "STALE row must be excluded from retrieval"
        assert results[0].summary_text == "Active summary"
    finally:
        await _cleanup()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_intent_tracker_concurrent_upserts_no_lost_update(db_session) -> None:
    """T065: two concurrent upsert_with_lock() calls on the same customer/thread
    must both persist — real Postgres row locking, not a mocked stand-in.

    Each call runs on its own AsyncSession/connection (mirrors two real request
    handlers racing on the same row); the final version must reflect both writes.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from services.memory.intent_tracker import IntentTracker

    customer_id = "concurrent_upsert_cust"
    thread_id = "concurrent_upsert_thread"
    tracker = IntentTracker()

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _upsert():
        async with session_factory() as session:
            result = await tracker.upsert_with_lock(customer_id, thread_id, db=session)
            await session.commit()
            return result

    try:
        result1, result2 = await asyncio.gather(_upsert(), _upsert())

        assert {result1.version, result2.version} == {1, 2}, (
            "both concurrent upserts must persist — one insert (v1) + one update (v2), "
            "no lost update"
        )
    finally:
        await engine.dispose()
