"""Why this exists: Centralized gateway for all AI model interactions.
What it does: Provides async wrappers for LiteLLM with fallback and latency tracking.
             Week 2 addition: NormalizedQuery schema + normalize_query() for
             query rewriting.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections import deque
from typing import Any, Literal

import logfire
from litellm import Router
from openinference.instrumentation import using_session
from openinference.semconv.trace import SpanAttributes
from opentelemetry import trace
from pydantic import BaseModel, Field

from core.ai_config import LITELLM_CONFIG
from core.config import settings

# ── Query normalisation schema (FR-004) ───────────────────────────────────────


class NormalizedQuery(BaseModel):
    """
    Why this exists: Structured representation of a cleaned user query (FR-004).
    No regex; produced via LiteLLM response_format (Pydantic model).
    """

    canonical: str = Field(
        description=("The cleaned, canonical form of the user query in its original language.")
    )
    detected_language: str = Field(description="Detected language code, e.g. 'vi' or 'en'.")
    intent: str = Field(
        description=(
            "Primary intent: INFO_QUERY | PRICING | COMPARISON | "
            "COMPLAINT | NEGOTIATION | AVAILABILITY | OTHER"
        )
    )
    extracted_keywords: list[str] = Field(
        description=("Up to 10 meaningful keywords extracted from the query for FTS enrichment.")
    )
    is_valid: bool = Field(
        default=True,
        description=(
            "False if the query is spam, gibberish, or has no product-related intent. "
            "Pipeline skips retrieval when False."
        ),
    )


# ── Retry-loop query rewrite schema (agentic-rag-retry-loop, ticket 2026) ─────


class RewrittenQuery(BaseModel):
    """
    Why this exists: Structured rewrite of a query that failed retrieval, used by the
    bounded RAG retry loop (`services/rag/pipeline.py::retrieve_with_retry`, AC-2026-007/008).
    The light model ONLY rewrites — it never scores confidence (ADR-002, no new scorer); the
    rewrite is used solely as the next retrieval query, never executed (RISK-002).
    """

    query: str = Field(
        description="Rewritten query preserving the original intent and product entities."
    )
    keeps_subject: bool = Field(
        description=(
            "False if the rewrite changed the question's subject/product — the retry loop "
            "discards the rewrite when False (no subject drift)."
        )
    )


# ── LLM usage metrics (WP3 — real ModelTrace numbers) ─────────────────────────


class LLMUsageMetrics(BaseModel):
    """Token/cost/latency numbers extracted from a LiteLLM response.

    Consumed by answer_node to write real model_traces rows (FR-008)
    instead of hardcoded zeros.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    latency_ms: float | None = None


def extract_llm_metrics(response: Any, latency_ms: float | None = None) -> LLMUsageMetrics:
    """Best-effort extraction of token usage + cost from a LiteLLM response.

    Never raises — returns zeroed metrics when the response carries no usable
    usage block (e.g. mocked responses in tests). Cost comes from
    litellm.completion_cost: 0.0 for local Ollama models (correct — they are
    free), real USD for cloud provider keys.
    """
    metrics = LLMUsageMetrics(latency_ms=latency_ms)
    usage = getattr(response, "usage", None)
    if usage is not None:
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(usage, field, 0) or 0
            # Guard against Mock objects: int(MagicMock()) == 1, so only
            # accept genuine numeric values (bool excluded).
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(metrics, field, int(value))
        if not metrics.total_tokens:
            metrics.total_tokens = metrics.prompt_tokens + metrics.completion_tokens
    try:
        import litellm

        metrics.cost = float(litellm.completion_cost(completion_response=response) or 0.0)
    except Exception:
        metrics.cost = 0.0
    return metrics


# ── Metadata enrichment schemas (Phase 1 - Ingestion) ──────────────────────────


class KeywordExtraction(BaseModel):
    """
    Why this exists: Structured keyword extraction for products (Phase 1).
    What it does: Extracts relevant keywords via LiteLLM response_format.
    """

    keywords: list[str] = Field(
        ...,
        min_length=3,
        max_length=10,
        description="3-10 keywords for hybrid search (FTS + vector)",
    )
    rationale: str = Field(description="Brief explanation of why these keywords were chosen")


class ProductMetadata(BaseModel):
    """
    Why this exists: Structured metadata for products (Phase 1 ingestion).
    What it does: Enriches products with specs, category, intent, keywords, summary.
    Enforces structure via Pydantic validation.
    """

    product_id: str = Field(..., description="SKU or unique product identifier")
    technical_specs: dict[str, Any] = Field(
        default_factory=dict,
        description="Technical specifications: voltage, frequency, thermal_limit, etc.",
    )
    keywords: list[str] = Field(
        ...,
        min_length=3,
        max_length=10,
        description="High-quality keywords for hybrid search",
    )
    seo_summary: str = Field(
        ...,
        max_length=100,
        description="SEO-friendly summary under 100 characters for display",
    )
    category: str = Field(
        ...,
        max_length=50,
        description="Product category for filtering/metadata signals",
    )
    intent: Literal["commercial", "consumer"] = Field(
        ...,
        description="Sales intent: commercial (B2B) or consumer (B2C)",
    )

    @classmethod
    def minimal(cls, product_id: str, name: str) -> ProductMetadata:
        """Fallback: minimal metadata when enrichment fails."""
        return cls(
            product_id=product_id,
            technical_specs={},
            keywords=[product_id.lower(), name.lower(), "product"],
            seo_summary=name[:50],
            category="unknown",
            intent="consumer",
        )


# Initialize LiteLLM Router for advanced routing and fallbacks
ai_router = Router(**LITELLM_CONFIG)


# ── Rate-limit aware resilience layer (2026-22-8 report §4.3) ────────────────
# Proactive client-side RPM throttle: free-tier cloud providers (Groq: 30 RPM)
# return persistent 429s once the ceiling is hit; the reactive cooldown in
# ai_config.py only kicks in AFTER a 429 has already degraded a turn. With
# LLM_RPM_LIMIT > 0 every chat completion waits its turn in a sliding window
# so the ceiling is never reached. 0 (default, local profiles) = disabled.


class _RpmLimiter:
    def __init__(self) -> None:
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        limit = settings.LLM_RPM_LIMIT
        if limit <= 0:
            return
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._stamps and now - self._stamps[0] >= 60.0:
                    self._stamps.popleft()
                if len(self._stamps) < limit:
                    self._stamps.append(now)
                    return
                wait = 60.0 - (now - self._stamps[0])
            logfire.info(
                "RPM throttle: waiting {wait:.1f}s (limit={limit})", wait=wait, limit=limit
            )
            await asyncio.sleep(max(wait, 0.05))


_rpm_limiter = _RpmLimiter()


# ── Universal JSON extractor (2026-22-8 report §4.1) ─────────────────────────
# Providers implement structured output differently: Ollama constrains
# generation against the JSON schema natively, while Groq/OpenAI rewrite
# response_format=PydanticModel into tool calling and hard-fail (400) on
# details like Python-style `False` booleans. Every structured-output call
# therefore goes through one uniform two-step contract:
#   1. native response_format=<schema>  (best quality where supported)
#   2. schema-in-prompt + response_format={"type": "json_object"} + repair
# so a provider quirk degrades to step 2 instead of crashing the node into
# its blind fallback (the INFO_QUERY paralysis described in the report).

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
# Python literals leaking into JSON output (`True`/`False`/`None`) — replace
# only when not inside a double-quoted string (best-effort, no full parser).
_PY_LITERAL_RE = re.compile(r'(?<!")\b(True|False|None)\b(?!")')
_PY_LITERAL_MAP = {"True": "true", "False": "false", "None": "null"}


def _repair_json_text(text: str) -> str:
    """Best-effort cleanup of near-JSON LLM output before parsing."""
    text = text.strip()
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    # Trim any prose around the outermost JSON object/array.
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start > 0:
        end = max(text.rfind("}"), text.rfind("]"))
        if end > start:
            text = text[start : end + 1]
    return _PY_LITERAL_RE.sub(lambda m: _PY_LITERAL_MAP[m.group(1)], text)


def _loads_structured(content: Any) -> dict[str, Any]:
    """Parse LLM structured-output content (str or already-parsed dict)."""
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        raise ValueError(f"unparseable structured content type: {type(content).__name__}")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = json.loads(_repair_json_text(content))
    if not isinstance(data, dict):
        raise ValueError("structured output is valid JSON but not an object")
    return data


def _with_schema_prompt(
    messages: list[dict[str, str]], schema: type[BaseModel] | None
) -> list[dict[str, str]]:
    """Append the JSON schema contract to the system message (step 2)."""
    instruction = (
        "\n\nRespond with a SINGLE JSON object only — no prose, no markdown fences. "
        "Booleans MUST be JSON lowercase true/false and missing values null."
    )
    if schema is not None:
        instruction += "\nThe object MUST match this JSON Schema:\n" + json.dumps(
            schema.model_json_schema(), ensure_ascii=False
        )
    out = [dict(m) for m in messages]
    for m in out:
        if m.get("role") == "system":
            m["content"] = f"{m['content']}{instruction}"
            return out
    return [{"role": "system", "content": instruction.strip()}, *out]


# LiteLLM traces are captured via HTTPXClientInstrumentor (registered in core/logging.py).
# Do NOT set litellm.success_callback = ["logfire"] — LiteLLM's own OTel integration
# tries to create a new TracerProvider, conflicting with logfire's ProxyTracerProvider
# (they are different types and OTel only allows one provider).
# HTTPX auto-instrumentation produces spans for every outbound call to Ollama/OpenAI.


class AIGateway:
    """
    Why this exists: Unified interface for AI operations (Chat & Embedding).
    Article V: Asynchronous I/O Mandate.
    Article X: Model Selection & Cost Efficiency.
    """

    @staticmethod
    async def complete(
        messages: list[dict[str, str]],
        model: str = "economy-chat",
        stream: bool = False,
        **kwargs,
    ) -> Any:
        """
        Why this exists: Generates text responses with automatic fallback.
        What it does: Wraps litellm.acompletion with latency monitoring.
        FR-016: Model switching latency < 2s.
        """
        # ── Trace Linking (SME Pro 2026) ──
        # Extract session_id from current OTel span (set by FastAPI router)
        current_span = trace.get_current_span()
        session_id = getattr(current_span, "attributes", {}).get(SpanAttributes.SESSION_ID)

        # v3-0 P3 (T09): when the fallback ladder drives this call it owns the
        # rung-to-rung fallback — the legacy in-method recursion must not fire.
        ladder_managed = bool(kwargs.pop("_ladder", False))

        start_time = time.perf_counter()

        try:
            logfire.info("AI Completion started: {model}", model=model)

            # Link LiteLLM sub-spans to the conversation session
            with using_session(session_id) if session_id else contextlib.nullcontext():
                kwargs.setdefault("max_tokens", 512)
                await _rpm_limiter.acquire()
                response = await ai_router.acompletion(
                    model=model, messages=messages, stream=stream, **kwargs
                )

            latency = time.perf_counter() - start_time
            logfire.info(
                "AI Completion finished: {model}, Latency: {latency:.4f}s",
                model=model,
                latency=latency,
            )

            return response

        except Exception as e:
            latency = time.perf_counter() - start_time
            logfire.error(
                "AI Completion failed: {model}, Latency: {latency:.4f}s, Error: {err}",
                model=model,
                latency=latency,
                err=str(e),
            )

            # FR-015: Manual fallback to economy-chat (local) if model call fails.
            # Skipped when the P3 ladder manages the call — it decides the next
            # rung (429 cooldown, intent policy) instead of this blind fallback.
            if not ladder_managed and model != "economy-chat":
                logfire.warn("Falling back to economy-chat due to error: {err}", err=str(e))
                return await AIGateway.complete(
                    messages=messages, model="economy-chat", stream=stream, **kwargs
                )
            raise e

    @staticmethod
    async def complete_json(
        messages: list[dict[str, str]],
        model: str = "economy-chat",
        schema: type[BaseModel] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Universal JSON extractor (2026-22-8 report §4.1) — always a dict.

        Step 1 uses the provider's native structured output (response_format=
        <schema>); any provider quirk (Groq tool-call validation 400s, refused
        json_schema, near-JSON text) drops to step 2: schema injected into the
        system prompt + response_format={"type": "json_object"} + repair parse.
        Raises only when BOTH steps fail — callers keep their own fallbacks.
        """
        if schema is not None:
            try:
                resp = await AIGateway.complete(
                    messages=messages, model=model, response_format=schema, **kwargs
                )
                return _loads_structured(resp.choices[0].message.content)
            except Exception as exc:
                logfire.warn(
                    "complete_json step 1 (native schema) failed on {model}: {err}",
                    model=model,
                    err=str(exc),
                )
        resp = await AIGateway.complete(
            messages=_with_schema_prompt(messages, schema),
            model=model,
            response_format={"type": "json_object"},
            **kwargs,
        )
        return _loads_structured(resp.choices[0].message.content)

    @staticmethod
    async def complete_structured[T: BaseModel](
        schema: type[T],
        messages: list[dict[str, str]],
        model: str = "economy-chat",
        **kwargs,
    ) -> T:
        """complete_json + Pydantic validation → a schema instance."""
        data = await AIGateway.complete_json(messages, model=model, schema=schema, **kwargs)
        return schema.model_validate(data)

    @staticmethod
    async def embed(
        input_text: str | list[str], model: str = "economy-embedding", **kwargs
    ) -> list[list[float]]:
        """
        Why this exists: Generates text embeddings for RAG and caching.
        What it does: Wraps litellm.aembedding with latency monitoring. When
        EMBED_MODEL uses the "local/" prefix, embeds in-process via fastembed
        (ONNX, CPU) instead — no Ollama server and no cloud key needed, which
        keeps the zero-cost path alive when chat runs on a cloud provider
        (e.g. Groq, which offers no embedding endpoint).
        """
        current_span = trace.get_current_span()
        session_id = getattr(current_span, "attributes", {}).get(SpanAttributes.SESSION_ID)

        start_time = time.perf_counter()

        # Ensure input is a list for consistent processing
        if isinstance(input_text, str):
            input_text = [input_text]

        try:
            logfire.info("AI Embedding started: {model}", model=model)

            with using_session(session_id) if session_id else contextlib.nullcontext():
                response = await ai_router.aembedding(model=model, input=input_text, **kwargs)

            latency = time.perf_counter() - start_time
            logfire.info(
                "AI Embedding finished: {model}, Latency: {latency:.4f}s",
                model=model,
                latency=latency,
            )

            # Extract embeddings from response
            from core.config import settings

            expected_dim = settings.EMBED_DIMENSION
            embeddings = [data["embedding"] for data in response.data]
            for emb in embeddings:
                if len(emb) != expected_dim:
                    logfire.error(
                        (
                            "Configuration Error: Model Mismatch — "
                            "embedding dimension {got} (expected {exp})"
                        ),
                        got=len(emb),
                        exp=expected_dim,
                        model=model,
                    )
                    raise ValueError(
                        f"Configuration Error: Model Mismatch — "
                        f"embedding dimension {len(emb)} (expected {expected_dim})"
                    )
            return embeddings

        except Exception as e:
            latency = time.perf_counter() - start_time
            logfire.error(
                "AI Embedding failed: {model}, Latency: {latency:.4f}s, Error: {err}",
                model=model,
                latency=latency,
                err=str(e),
            )
            raise e

    @staticmethod
    async def normalize_query(query: str) -> NormalizedQuery:
        """
        Why this exists: Cleans and structures Vietnamese/English queries before
        retrieval (FR-004).
        What it does: Uses LiteLLM response_format=NormalizedQuery to produce
        structured output. No regex parsing — Pydantic model enforced by LiteLLM.
        Uses light-chat (qwen3:0.6b) — normalization is a cheap, repetitive task.
        """
        start_time = time.perf_counter()

        # ── Heuristic pre-check (zero-cost, sub-ms) ───────────────────────────
        stripped = query.strip()
        if len(stripped) < 3 or stripped.replace(" ", "").isdigit():
            logfire.info("normalize_query: rejected by heuristic (too short / digit-only)")
            return NormalizedQuery(
                canonical=stripped,
                detected_language="unknown",
                intent="OTHER",
                extracted_keywords=[],
                is_valid=False,
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a query preprocessing assistant for a bilingual "
                    "(Vietnamese/English) SME product database.\n"
                    "IMPORTANT: Vietnamese (vi) and English (en) are BOTH valid "
                    "languages. Queries about prices, specs, features, availability, "
                    "or comparing products are ALWAYS valid (is_valid=true).\n"
                    "Only set is_valid=false for clear spam, random characters, or "
                    "content completely unrelated to products (e.g. 'asdfg', "
                    "'tell me a joke').\n\n"
                    "Normalize the user query:\n"
                    "- canonical: full clean question preserving the user's intent\n"
                    "- detected_language: vi | en | mixed\n"
                    "- intent: INFO_QUERY | PRICING | COMPARISON | COMPLAINT | "
                    "NEGOTIATION | AVAILABILITY | OTHER\n"
                    "- extracted_keywords: up to 10 search keywords\n"
                    "- is_valid: true unless spam/gibberish/off-topic\n\n"
                    "Examples:\n"
                    "Q: 'Giá Samsung S24 Ultra?' → is_valid=true, lang=vi, "
                    "intent=PRICING\n"
                    "Q: 'iPhone 15 Pro Max specs?' → is_valid=true, lang=en, "
                    "intent=INFO_QUERY\n"
                    "Q: 'xzxzxz' → is_valid=false\n\n"
                    "Respond only in the required JSON schema."
                ),
            },
            {"role": "user", "content": query},
        ]
        try:
            current_span = trace.get_current_span()
            session_id = getattr(current_span, "attributes", {}).get(SpanAttributes.SESSION_ID)

            with using_session(session_id) if session_id else contextlib.nullcontext():
                normalized = await AIGateway.complete_structured(
                    NormalizedQuery,
                    messages,
                    model="economy-chat",  # Same as generate_answer — no Ollama swap
                    temperature=0,
                )
            latency = time.perf_counter() - start_time
            logfire.info("normalize_query: {latency:.4f}s", latency=latency)
            return normalized
        except Exception as exc:
            latency = time.perf_counter() - start_time
            logfire.error(
                "normalize_query failed: {err} ({latency:.4f}s)",
                err=str(exc),
                latency=latency,
            )
            # Graceful fallback — return minimal normalised form without
            # blocking retrieval
            return NormalizedQuery(
                canonical=query.strip(),
                detected_language="unknown",
                intent="OTHER",
                extracted_keywords=query.strip().split()[:10],
                is_valid=True,  # assume valid on error to avoid false rejects
            )

    @staticmethod
    async def rewrite_query(original: str) -> RewrittenQuery:
        """
        Why this exists: Light-tier query rewrite for the bounded RAG retry loop
        (agentic-rag-retry-loop, ticket 2026 — AC-2026-007, AC-2026-008, AC-2026-010).
        What it does: Uses LiteLLM response_format=RewrittenQuery to rephrase a query that
        failed retrieval, preserving intent and product entities. Mirrors normalize_query's
        pattern exactly (heuristic pre-check, response_format, temperature=0). Calls
        `ai_router.acompletion` directly (not `AIGateway.complete`) so a failure never falls
        back to a premium tier — `rewrite_query` ALWAYS hardcodes `economy-chat` (light tier,
        never premium — AC-2026-010, EC-015). Never raises: any exception or unparseable
        output returns `keeps_subject=False`, which the caller treats as a failed/no-progress
        attempt (AC-2026-004, AC-2026-011).
        """
        start_time = time.perf_counter()

        # ── Heuristic pre-check (zero-cost, sub-ms) — mirrors normalize_query ─────
        stripped = original.strip()
        if len(stripped) < 3:
            logfire.info("rewrite_query: rejected by heuristic (too short)")
            return RewrittenQuery(query=stripped, keeps_subject=False)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a query-rewriting assistant for a bilingual "
                    "(Vietnamese/English) SME product database.\n"
                    "The previous retrieval for this query returned poor results. Rewrite "
                    "the query to improve search recall WITHOUT changing its meaning.\n"
                    "Rules:\n"
                    "- Preserve the original intent and EVERY product entity/name mentioned.\n"
                    "- Do NOT change the subject of the question (never switch to asking "
                    "about a different product).\n"
                    "- Expand abbreviations, fix typos, resolve vague phrasing, and add "
                    "synonyms that help full-text/vector search.\n"
                    "- keeps_subject: true unless your rewrite could not avoid changing the "
                    "subject — set false in that case so the rewrite is discarded.\n\n"
                    "Respond only in the required JSON schema."
                ),
            },
            {"role": "user", "content": original},
        ]
        try:
            current_span = trace.get_current_span()
            session_id = getattr(current_span, "attributes", {}).get(SpanAttributes.SESSION_ID)

            with using_session(session_id) if session_id else contextlib.nullcontext():
                rewritten = await AIGateway.complete_structured(
                    RewrittenQuery,
                    messages,
                    model="economy-chat",  # light tier, hardcoded — never premium (AC-2026-010)
                    temperature=0,
                )
            latency = time.perf_counter() - start_time
            logfire.info("rewrite_query: {latency:.4f}s", latency=latency)
            return rewritten
        except Exception as exc:
            latency = time.perf_counter() - start_time
            logfire.error(
                "rewrite_query failed: {err} ({latency:.4f}s)",
                err=str(exc),
                latency=latency,
            )
            # Graceful fallback — signal a failed rewrite; caller stops the loop
            return RewrittenQuery(query=original, keeps_subject=False)
