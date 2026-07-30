"""Why this exists: WP-V2-1 — answers must never state claims the retrieved
context does not back (hallucinated prices/specs) and must decline instead of
improvising when the catalog simply does not contain what was asked.
What it does: One economy-chat call grading a generated answer against its
retrieval context into a structured GroundednessVerdict (answerable +
supported + unsupported_claims). Mirrors AIGateway.normalize_query's pattern
(response_format Pydantic, temperature=0, graceful fallback). Fail-open: any
LLM/parse error returns a passing verdict so a checker outage never blocks
answers — the check is a quality gate, not an availability dependency.
"""

from __future__ import annotations

import time

import logfire
from pydantic import BaseModel, Field

from services.ai import ai_router

# Regeneration prompt appended on a groundedness failure — tightens the answer
# to context-only claims. Kept here (not constants.py) because it is owned by
# the groundedness loop, not the base RAG prompt.
STRICT_GROUNDING_SUFFIX = (
    "\nQUAN TRỌNG: Câu trả lời trước đã chứa thông tin KHÔNG có trong context. "
    "Chỉ được dùng các con số (giá, tồn kho) và thuộc tính xuất hiện NGUYÊN VĂN "
    "trong context sản phẩm. Nếu context không có thông tin được hỏi, nói rõ là "
    "không có thông tin — tuyệt đối không suy đoán."
)


class GroundednessVerdict(BaseModel):
    """Structured verdict from the groundedness self-check.

    answerable: the retrieval context actually contains the product / info the
        customer asked about (False → the honest move is to decline, e.g. an
        out-of-catalog product slipped past the similarity guard).
    supported: every factual claim in the answer (price, stock, spec) is backed
        by the context (False → regenerate with a stricter prompt).
    """

    answerable: bool
    supported: bool
    unsupported_claims: list[str] = Field(default_factory=list)


_PASS_VERDICT = GroundednessVerdict(answerable=True, supported=True)

_SYSTEM_PROMPT = (
    "You are a strict fact-checking judge for a Vietnamese sales assistant.\n"
    "You are given the retrieved PRODUCT CONTEXT, the CUSTOMER QUESTION, and "
    "the ASSISTANT ANSWER. Grade the answer:\n"
    "- answerable: true only if the context contains the specific product(s) "
    "or information the question asks about. If the customer asks about a "
    "product/category that does NOT appear in the context (e.g. asks for a "
    "fridge but the context only has phones), answerable=false — even when "
    "the answer politely redirects.\n"
    "- supported: true only if EVERY factual claim in the answer (price, "
    "stock, specification, promotion) appears in the context. An answer that "
    "honestly says information is missing is supported=true.\n"
    "- unsupported_claims: quote each claim not backed by the context "
    "(empty when supported=true).\n"
    "Respond only in the required JSON schema."
)


async def check_groundedness(query: str, answer: str, context: str) -> GroundednessVerdict:
    """Grade `answer` against `context` with one economy-chat call.

    Never raises: on any failure returns a passing verdict (fail-open) — the
    caller's behavior is then identical to the pre-WP-V2-1 pipeline.
    """
    if not answer.strip():
        return _PASS_VERDICT

    start_time = time.perf_counter()
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"PRODUCT CONTEXT:\n{context}\n\n"
                f"CUSTOMER QUESTION: {query}\n\n"
                f"ASSISTANT ANSWER:\n{answer}"
            ),
        },
    ]
    try:
        response = await ai_router.acompletion(
            model="economy-chat",
            messages=messages,
            response_format=GroundednessVerdict,
            temperature=0,
        )
        verdict = GroundednessVerdict.model_validate_json(response.choices[0].message.content)
        logfire.info(
            "groundedness verdict: answerable={a} supported={s} claims={n} ({t:.2f}s)",
            a=verdict.answerable,
            s=verdict.supported,
            n=len(verdict.unsupported_claims),
            t=time.perf_counter() - start_time,
        )
        return verdict
    except Exception as exc:
        logfire.warn("groundedness check failed (fail-open): {err}", err=str(exc))
        return _PASS_VERDICT
