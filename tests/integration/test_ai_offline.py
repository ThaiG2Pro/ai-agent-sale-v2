"""Why this exists: Verifies AIGateway behavior (embed / complete / fallback)
without any network dependency.

WP6 rework: these tests previously hit a real local Ollama (and, on fallback,
the internet) and flaked whenever the engine was down. LiteLLM's Ollama path
uses aiohttp (not httpx), so respx cannot intercept it — instead we mock the
LiteLLM ``Router`` boundary (``services.ai.ai_router``), which is exactly the
seam ``AIGateway`` wraps. The gateway's own logic (input normalization,
dimension validation, FR-015 manual fallback) still runs for real.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import settings
from services.ai import AIGateway


def _embedding_response(dim: int) -> SimpleNamespace:
    """Shape-compatible stand-in for litellm EmbeddingResponse."""
    return SimpleNamespace(data=[{"embedding": [0.1] * dim}])


def _completion_response(content: str) -> SimpleNamespace:
    """Shape-compatible stand-in for litellm ModelResponse."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.mark.asyncio
async def test_ai_gateway_embed_offline(monkeypatch):
    """
    Why this exists: Verifies embedding extraction + dimension validation
    (Article XII) with the router mocked out.
    """
    mock_aembedding = AsyncMock(return_value=_embedding_response(settings.EMBED_DIMENSION))
    monkeypatch.setattr("services.ai.ai_router.aembedding", mock_aembedding)

    embeddings = await AIGateway.embed(input_text="This is a test for offline embedding.")

    assert isinstance(embeddings, list)
    assert len(embeddings) == 1
    assert isinstance(embeddings[0], list)
    assert len(embeddings[0]) == settings.EMBED_DIMENSION
    # str input must be normalized to a list before hitting the router
    assert mock_aembedding.await_args.kwargs["input"] == ["This is a test for offline embedding."]


@pytest.mark.asyncio
async def test_ai_gateway_embed_dimension_mismatch_raises(monkeypatch):
    """
    Why this exists: A wrong embedding dimension is a config error (model
    mismatch) and must fail loudly, never be stored.
    """
    mock_aembedding = AsyncMock(return_value=_embedding_response(settings.EMBED_DIMENSION + 1))
    monkeypatch.setattr("services.ai.ai_router.aembedding", mock_aembedding)

    with pytest.raises(ValueError, match="Model Mismatch"):
        await AIGateway.embed(input_text="dimension check")


@pytest.mark.asyncio
async def test_ai_gateway_complete_offline(monkeypatch):
    """
    Why this exists: Verifies completion plumbing (Article XII) with the
    router mocked out.
    """
    mock_acompletion = AsyncMock(return_value=_completion_response("hello"))
    monkeypatch.setattr("services.ai.ai_router.acompletion", mock_acompletion)

    response = await AIGateway.complete(
        messages=[{"role": "user", "content": "Say 'hello' in exactly one word."}]
    )

    assert response is not None
    assert hasattr(response, "choices")
    content = response.choices[0].message.content
    assert len(content) > 0


@pytest.mark.asyncio
async def test_ai_gateway_fallback_logic(monkeypatch):
    """
    Why this exists: Verifies the FR-015 manual fallback — when economy-chat
    fails, AIGateway.complete retries once on premium-chat.
    """
    mock_acompletion = AsyncMock(
        side_effect=[
            ConnectionError("local engine unreachable"),
            _completion_response("fallback answer"),
        ]
    )
    monkeypatch.setattr("services.ai.ai_router.acompletion", mock_acompletion)

    response = await AIGateway.complete(
        messages=[{"role": "user", "content": "test"}], model="economy-chat"
    )

    assert response is not None
    assert response.choices[0].message.content == "fallback answer"
    assert mock_acompletion.await_count == 2
    assert mock_acompletion.await_args_list[0].kwargs["model"] == "economy-chat"
    assert mock_acompletion.await_args_list[1].kwargs["model"] == "premium-chat"


@pytest.mark.asyncio
async def test_ai_gateway_non_economy_failure_propagates(monkeypatch):
    """
    Why this exists: Fallback is one-shot — a premium-chat failure must raise,
    not loop forever (FR-015 boundary).
    """
    mock_acompletion = AsyncMock(side_effect=ConnectionError("still unreachable"))
    monkeypatch.setattr("services.ai.ai_router.acompletion", mock_acompletion)

    with pytest.raises(ConnectionError):
        await AIGateway.complete(
            messages=[{"role": "user", "content": "test"}], model="premium-chat"
        )
    assert mock_acompletion.await_count == 1
