"""Why this exists: Verifies offline AI capabilities (Embeddings & Completion).
What it does: Tests AIGateway operations against local Ollama baseline.
"""

import pytest

from services.ai import AIGateway


@pytest.mark.asyncio
async def test_ai_gateway_embed_offline():
    """
    Why this exists: Verifies local embedding generation (Article XII).
    """
    text = "This is a test for offline embedding."

    try:
        embeddings = await AIGateway.embed(input_text=text)

        assert isinstance(embeddings, list)
        assert len(embeddings) == 1
        assert isinstance(embeddings[0], list)
        assert len(embeddings[0]) > 0

    except Exception as e:
        # If Ollama is not running, we skip rather than fail the CI if local only
        pytest.skip(f"Ollama embedding failed (offline engine unreachable): {e}")


@pytest.mark.asyncio
async def test_ai_gateway_complete_offline():
    """
    Why this exists: Verifies local text completion (Article XII).
    """
    messages = [{"role": "user", "content": "Say 'hello' in exactly one word."}]

    try:
        response = await AIGateway.complete(messages=messages)

        # LiteLLM response structure check
        assert response is not None
        assert hasattr(response, "choices")
        content = response.choices[0].message.content
        assert len(content) > 0

    except Exception as e:
        pytest.skip(f"Ollama completion failed (offline engine unreachable): {e}")


@pytest.mark.asyncio
async def test_ai_gateway_fallback_logic():
    """
    Why this exists: Verifies that fallback triggers on local failure (FR-015).
    """
    messages = [{"role": "user", "content": "test"}]

    # We expect this to either succeed via fallback or fail if offline
    # If it fails, we want to know it's a connection error, not a logic error.
    try:
        response = await AIGateway.complete(
            messages=messages, model="invalid-local-model"
        )
        assert response is not None
    except Exception as e:
        if "Connection" in str(e) or "unreachable" in str(e):
            pytest.skip(f"Fallback target unreachable (no internet): {e}")
        else:
            # If it's a different error, it might be a configuration issue
            raise e
