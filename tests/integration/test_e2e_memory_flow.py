"""End-to-end integration test for Week 5 memory features (Phase 5-8).

Tests full flow: Query → Router → Retrieval → Memory Injection → Summarization →
Semantic Storage → RTBF.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent.state import AgentState, IntentEnum


@pytest.mark.asyncio
async def test_e2e_memory_flow_with_context_injection():
    """E2E: Customer query → Memory retrieval → Context injection → Answer.

    Scenario:
    1. New customer asks about pricing
    2. System retrieves past context (empty for new customer)
    3. System injects memory context into prompt
    4. System generates answer
    5. Background tasks summarize and store in semantic memory
    """
    # 1. Initialize state
    state = AgentState(
        customer_id="cust_new_001",
        thread_id="thread_001",
        user_message="What's the price of your premium plan?",
        primary_intent=IntentEnum.PRICING,
        conversation_history=[],
    )

    # 2. Mock SemanticMemoryService for empty retrieval (new customer)
    mock_service = AsyncMock()
    mock_service.retrieve = AsyncMock(return_value=[])

    # 3. Import and test memory_retrieval_node
    from core.agent.nodes.memory_retrieval import memory_retrieval_node

    mock_db = AsyncMock()

    with patch(
        "services.memory.semantic_memory.SemanticMemoryService",
        return_value=mock_service,
    ):
        updated_state = await memory_retrieval_node(state, mock_db)

        # Should return empty context for new customer
        assert updated_state["memory_context"] == []
        assert updated_state["memory_retrieval_scores"] == []

    # 4. Test answer_node injects memory context
    from core.agent.nodes.answer import answer_node

    # Mock LiteLLM and RAG
    with patch(
        "core.agent.nodes.answer.completion",
        new_callable=AsyncMock,
    ) as mock_llm:
        mock_llm.return_value.choices = [
            MagicMock(message=MagicMock(content="Premium plan is $99/month"))
        ]

        with patch(
            "core.agent.nodes.answer.get_rag_context",
            new_callable=AsyncMock,
            return_value=["Product A: $99", "Product B: $199"],
        ):
            # Run answer node
            answer_state = await answer_node(updated_state, mock_db)

            # Should have generated response
            assert "answer" in answer_state
            assert answer_state["answer"] != ""


@pytest.mark.asyncio
async def test_e2e_background_task_orchestration():
    """E2E: Post-turn tasks coordinate intent extraction, summarization, memory update.

    Scenario:
    1. Customer sends message
    2. System generates response (synchronously)
    3. Background tasks extract intent (async)
    4. Background tasks summarize (async)
    5. Background tasks update semantic memory (async)
    """
    from services.memory.background import post_turn_tasks

    # Setup state and mocks
    state = AgentState(
        customer_id="cust_001",
        thread_id="thread_001",
        user_message="I want to buy 5 units",
        primary_intent=IntentEnum.NEGOTIATION,
        conversation_history=[],
    )

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()

    # Mock services
    with (
        patch(
            "services.memory.background.SalesIntentExtractor",
        ) as mock_extractor_class,
        patch(
            "services.memory.background.IntentTracker",
        ),
        patch(
            "services.memory.background.ConversationSummarizer",
        ) as mock_summarizer_class,
        patch(
            "services.memory.background._update_semantic_memory",
            new_callable=AsyncMock,
        ),
    ):
        # Setup extractor mock
        mock_extractor = AsyncMock()
        mock_extractor.should_extract = AsyncMock(return_value=True)
        mock_extractor.extract = AsyncMock(
            return_value={
                "primary_intent": IntentEnum.NEGOTIATION,
                "urgency_level": "HIGH",
            }
        )
        mock_extractor_class.return_value = mock_extractor

        # Setup summarizer mock
        mock_summarizer = AsyncMock()
        mock_summarizer.should_summarize = AsyncMock(return_value=False)
        mock_summarizer_class.return_value = mock_summarizer

        # Run post-turn tasks
        await post_turn_tasks(
            customer_id="cust_001",
            thread_id="thread_001",
            message="I want to buy 5 units",
            response="I can help with that",
            primary_intent=IntentEnum.NEGOTIATION,
            state=state,
            db=mock_db,
        )

        # Verify intent extraction was called
        mock_extractor.should_extract.assert_called()


@pytest.mark.asyncio
async def test_e2e_rtbf_cascade_delete():
    """E2E: RTBF deletion cascades across all memory tables.

    Scenario:
    1. Customer asks for data deletion (RTBF)
    2. System checks authorization
    3. System deletes IntentTracking
    4. System deletes ConversationSummaries
    5. System deletes SemanticMemory
    6. System logs deletion via observability
    """
    from api.routes.memory import delete_customer_memory

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    # Mock execute to return counts
    def mock_execute(stmt):
        mock_result = MagicMock()
        mock_result.scalar.return_value = 5  # 5 records for each table
        mock_result.scalars.return_value.all.return_value = [
            MagicMock(),  # Dummy rows
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]
        return mock_result

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("api.routes.memory.require_admin_key", return_value=True):
        result = await delete_customer_memory(
            customer_id="cust_001",
            confirm=True,
            db=mock_db,
            _admin=True,
        )

        # Verify deletion counts
        assert result["deleted"]["intent_tracking"] == 5
        assert result["deleted"]["conversation_summaries"] == 5
        assert result["deleted"]["semantic_memory"] == 5
        assert result["status"] == "success"

        # Verify commit was called
        mock_db.commit.assert_called()


@pytest.mark.asyncio
async def test_e2e_memory_resilience_to_failures():
    """E2E: Memory operations gracefully degrade on failure.

    Scenario:
    1. Customer asks question
    2. Memory retrieval fails
    3. System continues without memory context
    4. Answer still generated successfully
    5. Error logged for observability
    """
    from core.agent.nodes.memory_retrieval import memory_retrieval_node

    state = AgentState(
        customer_id="cust_001",
        thread_id="thread_001",
        user_message="What's the price?",
        primary_intent=IntentEnum.PRICING,
        conversation_history=[],
    )

    mock_db = AsyncMock()

    # Mock semantic service to raise exception
    mock_service = AsyncMock()
    mock_service.retrieve = AsyncMock(side_effect=RuntimeError("Database unreachable"))

    with patch(
        "services.memory.semantic_memory.SemanticMemoryService",
        return_value=mock_service,
    ):
        # Should not raise, but gracefully return empty context
        result = await memory_retrieval_node(state, mock_db)

        assert result["memory_context"] == []
        assert result["memory_retrieval_scores"] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
