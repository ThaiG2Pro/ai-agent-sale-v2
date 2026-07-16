"""Why this exists: TDD for `AIGateway.rewrite_query` (agentic-rag-retry-loop, ticket 2026,
ADR-005) — the light-tier, intent-preserving query rewrite used by the RAG retry loop.

What it does: Verifies the heuristic pre-check, hardcoded light-tier model selection
(AC-2026-010), structured-output parsing, and the graceful-fallback contract the retry loop
depends on (AC-2026-004, AC-2026-011: `rewrite_query` NEVER raises). No network/Ollama — all
`ai_router.acompletion` calls are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.ai import AIGateway, RewrittenQuery


def _mock_llm_response(query: str, keeps_subject: bool = True):
    """Builds a fake LiteLLM completion response carrying a RewrittenQuery JSON payload."""
    payload = RewrittenQuery(query=query, keeps_subject=keeps_subject).model_dump_json()
    response = AsyncMock()
    response.choices = [AsyncMock(message=AsyncMock(content=payload))]
    return response


class TestHeuristicPreCheck:
    @pytest.mark.asyncio
    async def test_too_short_query_rejected_without_llm_call(self):
        """Sub-3-char input never reaches the LLM (mirrors normalize_query's heuristic)."""
        with patch("services.ai.ai_router.acompletion", new_callable=AsyncMock) as mock_llm:
            result = await AIGateway.rewrite_query("ab")

        mock_llm.assert_not_called()
        assert result.keeps_subject is False

    @pytest.mark.asyncio
    async def test_whitespace_only_rejected_without_llm_call(self):
        with patch("services.ai.ai_router.acompletion", new_callable=AsyncMock) as mock_llm:
            result = await AIGateway.rewrite_query("   ")

        mock_llm.assert_not_called()
        assert result.keeps_subject is False


class TestLightTierEnforcement:
    @pytest.mark.asyncio
    async def test_hardcodes_economy_chat_model(self):
        """AC-2026-010, EC-015: model is ALWAYS economy-chat — no model parameter exists to
        override it with a premium/paid tier."""
        with patch(
            "services.ai.ai_router.acompletion",
            new_callable=AsyncMock,
            return_value=_mock_llm_response("giá của sản phẩm này là bao nhiêu"),
        ) as mock_llm:
            await AIGateway.rewrite_query("giá sản phẩm")

        mock_llm.assert_called_once()
        assert mock_llm.call_args.kwargs["model"] == "economy-chat"

    @pytest.mark.asyncio
    async def test_temperature_zero_and_structured_output(self):
        with patch(
            "services.ai.ai_router.acompletion",
            new_callable=AsyncMock,
            return_value=_mock_llm_response("rewritten query"),
        ) as mock_llm:
            await AIGateway.rewrite_query("original query")

        assert mock_llm.call_args.kwargs["temperature"] == 0
        assert mock_llm.call_args.kwargs["response_format"] is RewrittenQuery

    @pytest.mark.asyncio
    async def test_prompt_instructs_subject_preservation(self):
        """AC-2026-008: the rewrite prompt must instruct the model to preserve intent/product
        entities and forbid subject drift (best-effort mitigation for G2)."""
        with patch(
            "services.ai.ai_router.acompletion",
            new_callable=AsyncMock,
            return_value=_mock_llm_response("rewritten query"),
        ) as mock_llm:
            await AIGateway.rewrite_query("original query")

        messages = mock_llm.call_args.kwargs["messages"]
        system_content = next(m["content"] for m in messages if m["role"] == "system")
        assert "subject" in system_content.lower()
        assert "product entit" in system_content.lower()
        assert "keeps_subject" in system_content.lower()


class TestSuccessfulRewrite:
    @pytest.mark.asyncio
    async def test_successful_rewrite_parses_structured_output(self):
        """AC-2026-007: successful rewrite returns the parsed RewrittenQuery."""
        with patch(
            "services.ai.ai_router.acompletion",
            new_callable=AsyncMock,
            return_value=_mock_llm_response("tủ lạnh Samsung 300 lít giá bao nhiêu"),
        ):
            result = await AIGateway.rewrite_query("tủ lạnh giá")

        assert result.query == "tủ lạnh Samsung 300 lít giá bao nhiêu"
        assert result.keeps_subject is True

    @pytest.mark.asyncio
    async def test_english_query_rewrite_preserves_flag(self):
        """AC-2026-008: works across VN/EN — keeps_subject flag is honored verbatim."""
        with patch(
            "services.ai.ai_router.acompletion",
            new_callable=AsyncMock,
            return_value=_mock_llm_response("What is the battery life of the WidgetPro laptop?"),
        ):
            result = await AIGateway.rewrite_query("WidgetPro battery")

        assert "WidgetPro" in result.query
        assert result.keeps_subject is True


class TestGracefulFallback:
    @pytest.mark.asyncio
    async def test_malformed_json_returns_keeps_subject_false(self):
        """AC-2026-004: unparseable model output never raises — falls back safely."""
        bad_response = AsyncMock()
        bad_response.choices = [AsyncMock(message=AsyncMock(content="not valid json {{{"))]
        with patch(
            "services.ai.ai_router.acompletion",
            new_callable=AsyncMock,
            return_value=bad_response,
        ):
            result = await AIGateway.rewrite_query("original query")

        assert result.keeps_subject is False
        assert result.query == "original query"  # fallback echoes the original

    @pytest.mark.asyncio
    async def test_llm_exception_returns_keeps_subject_false(self):
        """AC-2026-011: rewriter timeout/exception never propagates — graceful fallback."""
        with patch(
            "services.ai.ai_router.acompletion",
            new_callable=AsyncMock,
            side_effect=TimeoutError("ollama unreachable"),
        ):
            result = await AIGateway.rewrite_query("original query")

        assert result.keeps_subject is False
        assert result.query == "original query"

    @pytest.mark.asyncio
    async def test_never_raises_for_any_failure_mode(self):
        """Structural guarantee the retry loop depends on: rewrite_query is a pure
        best-effort call — the caller's own try/except is defense in depth, not the only
        guard."""
        with patch(
            "services.ai.ai_router.acompletion",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection refused"),
        ):
            try:
                result = await AIGateway.rewrite_query("a perfectly normal query")
            except Exception as exc:  # pragma: no cover - must never happen
                pytest.fail(f"rewrite_query raised unexpectedly: {exc}")

        assert result.keeps_subject is False
