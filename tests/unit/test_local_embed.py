"""Why this exists: WP-V2-0 added a "local/" EMBED_MODEL path (fastembed, in-process)
so the RAG stack runs without Ollama when chat is served by a cloud provider.
What it does: unit tests for alias resolution, the embed() routing decision, and
the dimension guard — fastembed itself is mocked (no model download in unit tests).
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

import services.ai as ai_module
from services.ai import AIGateway, _resolve_local_embed_id


def test_alias_resolves_to_hf_model_id():
    resolved = _resolve_local_embed_id("local/multilingual-e5-large")
    assert resolved == "intfloat/multilingual-e5-large"


def test_unknown_alias_passes_through_verbatim():
    assert _resolve_local_embed_id("local/BAAI/bge-m3") == "BAAI/bge-m3"


class _FakeEmbedder:
    def __init__(self, dim: int):
        self.dim = dim
        self.seen: list[list[str]] = []

    def embed(self, texts):
        self.seen.append(list(texts))
        return [np.zeros(self.dim) for _ in texts]


@pytest.mark.asyncio
async def test_embed_routes_to_local_and_skips_router(monkeypatch):
    fake = _FakeEmbedder(dim=1024)
    monkeypatch.setattr(ai_module, "_local_embedder", fake)
    with patch("core.config.settings.EMBED_MODEL", "local/multilingual-e5-large"):
        vecs = await AIGateway.embed(["xin chào", "hello"])
    assert len(vecs) == 2
    assert len(vecs[0]) == 1024
    assert fake.seen == [["xin chào", "hello"]]


@pytest.mark.asyncio
async def test_local_embed_dimension_guard(monkeypatch):
    monkeypatch.setattr(ai_module, "_local_embedder", _FakeEmbedder(dim=768))
    with patch("core.config.settings.EMBED_MODEL", "local/multilingual-e5-large"):
        with pytest.raises(ValueError, match="Model Mismatch"):
            await AIGateway.embed("xin chào")


@pytest.mark.asyncio
async def test_string_input_wrapped_to_list(monkeypatch):
    fake = _FakeEmbedder(dim=1024)
    monkeypatch.setattr(ai_module, "_local_embedder", fake)
    with patch("core.config.settings.EMBED_MODEL", "local/multilingual-e5-large"):
        vecs = await AIGateway.embed("một câu duy nhất")
    assert len(vecs) == 1
    assert fake.seen == [["một câu duy nhất"]]
