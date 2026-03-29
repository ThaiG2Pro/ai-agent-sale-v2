"""Conversation Summarization Service (Phase 6, T091-T111).

Why: Reduce token usage by compressing long message histories into summaries.
What:
- ConversationSummarizer orchestrates LLM summarization pipeline
- Intelligently detects when summaries should be created (T092)
- Saves summaries to DB with metadata (T102)
- Integrates with background tasks for non-blocking execution (T104)

Article XI: All models must be configurable via settings. Economy model by default.
FR-004: Summary must capture intent, open questions, and products discussed.
FR-005: Answer node must compress context when summaries exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.agent.state import ConversationSummaryOutput
from core.config import settings
from services.ai import AIGateway

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ConversationSummarizer:
    """Orchestrates conversation summarization for long-running threads.

    Intelligently detects when summaries should be created based on:
    - Number of messages since last summary
    - Whether a summary already exists
    - Configurable thresholds from settings

    FR-004: Captures sales intent, open questions, and products discussed.
    """

    # Configuration constants (from Article II: settings-first)
    DEFAULT_SUMMARY_THRESHOLD = getattr(settings, "MEMORY_SUMMARY_THRESHOLD", 20)
    DEFAULT_RESUMMARY_TRIGGER = getattr(
        settings, "MEMORY_RESUMMARY_TRIGGER", 10
    )  # Re-summarize after 10 new messages

    def __init__(self):
        """Initialize the summarizer."""
        self.summary_model = settings.LIGHT_CHAT_MODEL
        self.summary_threshold = self.DEFAULT_SUMMARY_THRESHOLD
        self.resummary_trigger = self.DEFAULT_RESUMMARY_TRIGGER

    @staticmethod
    def should_summarize(
        message_count: int,
        has_existing_summary: bool,
        messages_since_last_summary: int,
    ) -> bool:
        """Determine if conversation should be summarized.

        T092: Returns True if:
        1. No summary exists AND message_count >= THRESHOLD, OR
        2. Summary exists AND messages_since_last_summary >= RESUMMARY_TRIGGER

        Args:
            message_count: Total messages in conversation
            has_existing_summary: Whether a summary already exists for this thread
            messages_since_last_summary: How many messages since last summary

        Returns:
            bool: True if summarization should proceed
        """
        threshold = ConversationSummarizer.DEFAULT_SUMMARY_THRESHOLD
        resummary_trigger = ConversationSummarizer.DEFAULT_RESUMMARY_TRIGGER

        # First summary at threshold
        if not has_existing_summary and message_count >= threshold:
            return True

        # Re-summarize after enough new messages
        if has_existing_summary and messages_since_last_summary >= resummary_trigger:
            return True

        return False

    async def summarize(
        self,
        messages: list[dict],
        session_id: str,
    ) -> ConversationSummaryOutput:
        """Summarize conversation using LLM (Article XII: use economy model).

        T097: Calls LiteLLM with structured output format.
        FR-004: Captures intent, open questions, and products discussed.

        Args:
            messages: List of conversation messages (each with role/content)
            session_id: Session identifier for logging

        Returns:
            ConversationSummaryOutput with summary_text, products, open_questions

        Raises:
            ValueError: If messages list is empty (T101)
        """
        if not messages:
            raise ValueError("Cannot summarize empty conversation")

        # Format conversation for LLM
        conversation_text = "\n".join(
            f"[{m.get('role', 'user').upper()}]: {m.get('content', '')}" for m in messages
        )

        # T097: Use economy model for summarization (Article XII)
        prompt = f"""Summarize the following customer support conversation in Vietnamese.
Focus on:
1. Main customer intent and needs
2. Products mentioned or discussed
3. Open questions or unresolved issues

Conversation:
{conversation_text}"""

        result = await AIGateway.complete(
            model=self.summary_model,
            messages=[{"role": "user", "content": prompt}],
            response_format=ConversationSummaryOutput,
        )

        # Extract the summary output
        if hasattr(result, "parsed"):
            summary_output = result.parsed
        else:
            # Fallback: create from text response
            from core.agent.state import ConversationSummaryOutput as CSO

            response_text = result.choices[0].message.content
            summary_output = CSO(
                summary_text=response_text,
                products_discussed=[],
                open_questions=[],
            )

        # Add metadata
        summary_output.summary_model = self.summary_model

        return summary_output

    @staticmethod
    async def save_summary(
        summary: ConversationSummaryOutput,
        session_id: str,
        customer_id: str,
        turn_count: int,
        db: AsyncSession,
    ) -> None:
        """Save summary to database (T102).

        Stores summary metadata for later retrieval and semantic memory updates.

        Args:
            summary: ConversationSummaryOutput from LLM
            session_id: Session identifier
            customer_id: Customer identifier
            turn_count: Message count when summary was created
            db: Async database session
        """
        from sqlalchemy import insert

        from models.schema import ConversationSummary

        stmt = insert(ConversationSummary).values(
            session_id=session_id,
            customer_id=customer_id,
            turn_count_at_summary=turn_count,
            summary_text=summary.summary_text,
            summary_model=summary.summary_model,
            products_discussed=summary.products_discussed,
            open_questions=summary.open_questions,
        )

        await db.execute(stmt)
        await db.commit()
