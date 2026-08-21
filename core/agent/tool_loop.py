"""
Why this exists: v3-0 P4 (T02/T08 4.1) — the router-enum keeps the 80% path
cheap, but ambiguous advisory turns (~20%) benefit from letting the premium
model look things up instead of answering from one fixed retrieval pass.
What it does: A bounded tool-calling loop on the premium tier (Groq 70b) with
guardrails G1-G8: G1 read-only tools only (search_products, check_inventory);
G2 loop <= TOOL_LOOP_MAX_HOPS (2); G3 Pydantic-validated tool args with ONE
retry on invalid; G4 English tool schema; G8 <= TOOL_LOOP_MAX_CALLS (3) cloud
calls per turn; on 429/any failure → single-shot local fallback (qwen3-4b) or
None so the caller keeps the normal path. Order/HITL stay on the state
machine — this loop NEVER executes writes. It is the FIRST feature the
degrade ladder turns off (3.1). Kill switch: TOOL_LOOP_ENABLED.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, ValidationError

from core.config import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Advisory intents only — the 20% path. Order/HITL keep the state machine.
ADVISORY_INTENTS = frozenset({"INFO_QUERY", "PRICING", "COMPARISON", "AVAILABILITY"})


class SearchProductsArgs(BaseModel):
    """G3: validated args for the read-only product search tool."""

    query: str = Field(min_length=2, max_length=300)
    top_k: int = Field(default=3, ge=1, le=5)


class CheckInventoryArgs(BaseModel):
    """G3: validated args for the read-only inventory lookup tool."""

    sku: str = Field(min_length=1, max_length=100)


# G4: English tool schema (small models parse it more reliably).
_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Search the product catalog (read-only). Returns name, SKU, price and description snippets for the best matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search text"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_inventory",
            "description": "Check current stock level for one product SKU (read-only).",
            "parameters": {
                "type": "object",
                "properties": {"sku": {"type": "string", "description": "Product SKU"}},
                "required": ["sku"],
            },
        },
    },
]

_SYSTEM_PROMPT = (
    "You are a Vietnamese e-commerce sales consultant with READ-ONLY catalog "
    "tools. Use the tools to look up facts you are unsure about (products, "
    "prices, stock), then answer the customer's question in natural "
    "Vietnamese. Never invent products or prices — only state what tool "
    "results contain. You cannot place, modify or cancel orders. "
    "Answer concisely and end with a friendly sales call-to-action."
)


async def _exec_tool(name: str, args: dict[str, Any], db: AsyncSession) -> str:
    """G1: read-only tool execution. Returns a compact JSON string result."""
    if name == "search_products":
        parsed = SearchProductsArgs.model_validate(args)
        from services.rag.retrieval import search_products

        rows = await search_products(db, parsed.query, top_k=parsed.top_k)
        slim = [
            {
                "name": r.get("name"),
                "sku": r.get("sku"),
                "price": r.get("price"),
                "text": str(r.get("text") or r.get("description") or "")[:300],
            }
            for r in rows[: parsed.top_k]
        ]
        return json.dumps(slim, ensure_ascii=False)
    if name == "check_inventory":
        parsed = CheckInventoryArgs.model_validate(args)
        from core.agent.tools import execute_inventory_lookup

        result = await execute_inventory_lookup(parsed.sku)
        if result.success:
            return json.dumps(
                {"sku": result.data.sku, "stock_level": result.data.stock_level},
                ensure_ascii=False,
            )
        return json.dumps({"error": result.error or "lookup failed"}, ensure_ascii=False)
    raise ValueError(f"unknown tool: {name}")


async def run_tool_loop(
    user_message: str,
    db: AsyncSession,
    context_note: str = "",
) -> tuple[str | None, str | None]:
    """Run the bounded advisory tool loop. Returns (answer, model_used).

    (None, None) means the caller must keep the normal single-shot path —
    every failure mode lands here (429, hop/call caps without a final answer,
    invalid tool args after the one retry, unavailable premium rung).
    """
    if not settings.TOOL_LOOP_ENABLED:
        return None, None

    # 3.1: this is the first feature OFF when degraded — skip when the premium
    # rung is cooling down or the daily token budget is (almost) spent.
    from services import resilience

    if not resilience._rung_available("premium-chat"):
        return None, None
    if await resilience.premium_budget_exhausted(db):
        return None, None

    from services.ai import AIGateway, extract_llm_metrics

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (f"{context_note}\n\n" if context_note else "") + user_message,
        },
    ]

    calls = 0
    hops = 0
    validation_retried = False
    try:
        while calls < settings.TOOL_LOOP_MAX_CALLS:
            calls += 1
            result = await AIGateway.complete(
                model="premium-chat",
                messages=messages,
                tools=_TOOLS_SCHEMA,
                tool_choice="auto",
                _ladder=True,  # loop owns its fallback — no blind recursion
            )
            metrics = extract_llm_metrics(result)
            await resilience.add_token_usage(db, "premium-chat", metrics.total_tokens)

            choice = result.choices[0].message
            tool_calls = getattr(choice, "tool_calls", None) or []
            if not tool_calls:
                content = (choice.content or "").strip()
                return (content or None), ("premium-tool-loop" if content else None)

            # G2: hop cap — a hop is one round of tool execution.
            hops += 1
            if hops > settings.TOOL_LOOP_MAX_HOPS:
                logger.info("tool loop: hop cap reached without final answer")
                return None, None

            messages.append(
                {
                    "role": "assistant",
                    "content": choice.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    tool_result = await _exec_tool(tc.function.name, args, db)
                except (ValidationError, json.JSONDecodeError, ValueError) as ve:
                    # G3: one retry — feed the validation error back once.
                    if validation_retried:
                        logger.info("tool loop: repeated invalid tool args — bailing")
                        return None, None
                    validation_retried = True
                    tool_result = json.dumps({"error": f"invalid arguments: {ve}"})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result,
                    }
                )
        logger.info("tool loop: call cap reached without final answer")
        return None, None
    except Exception as exc:
        # 429 / provider failure → single-shot LOCAL fallback (qwen3-4b), no tools.
        klass = resilience._classify_error(exc)
        if klass == "rate_limit":
            resilience._cool_down("premium-chat", settings.LLM_429_COOLDOWN_S)
        logger.warning("tool loop failed (%s) — local single-shot fallback", exc)
        try:
            result = await AIGateway.complete(
                model="qwen3-4b",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                _ladder=True,
            )
            content = (result.choices[0].message.content or "").strip()
            return (content or None), ("qwen3-4b" if content else None)
        except Exception:
            return None, None
