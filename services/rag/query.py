"""Query understanding: classification and normalization."""

from typing import Literal

from services.rag.constants import ACTION_VERBS


def classify_query(query: str) -> Literal["short", "long", "ambiguous"]:
    """
    Why this exists: Drives adaptive TopK for cost efficiency (FR-015).
    Rules (deterministic, word-count + keyword heuristic):
    - short:    word_count ≤ 10
    - long:     11 ≤ word_count ≤ 20
    - ambiguous: word_count > 20 AND (no action verb AND no capitalised proper noun)
    - >15 words with action verb or proper noun → "long" (safe fallback, TopK 15)
    """
    words = query.split()
    wc = len(words)
    if wc <= 10:
        return "short"

    has_action_verb = any(word.lower().strip("?,.:;!") in ACTION_VERBS for word in words)
    has_proper_noun = any(word[0].isupper() for word in words if len(word) > 1)

    if wc <= 20:
        return "long"

    if has_action_verb or has_proper_noun:
        return "long"

    return "ambiguous"


def compute_adaptive_topk(query: str, intent: str | None = None) -> int:
    """
    Why this exists: Cost efficiency (FR-015).
    Intent-first override (reduces tokens for focused queries):
      PRICING / INFO_QUERY → 5  (single product, specific fact)
      COMPARISON           → 10 (two products, cross-match needed)
    Fallback by word-count: short → 5, long → 15, ambiguous → 20.
    """
    if intent in ("PRICING", "INFO_QUERY"):
        return 5
    if intent == "COMPARISON":
        return 10
    return {"short": 5, "long": 15, "ambiguous": 20}[classify_query(query)]


def format_recent_history(
    messages: list, current_message: str | None = None, max_messages: int = 4
) -> str:
    """Format the last N messages as a compact transcript block."""
    lines = []
    prior = list(messages or [])
    if prior and getattr(prior[-1], "content", None) == current_message:
        prior = prior[:-1]
    for m in prior[-max_messages:]:
        content = getattr(m, "content", None) or (
            m.get("content") if isinstance(m, dict) else str(m)
        )
        if not content:
            continue
        is_human = (
            getattr(m, "type", "") == "human"
            or getattr(m, "role", "") == "user"
            or m.__class__.__name__ == "HumanMessage"
        )
        role = "Khách hàng" if is_human else "Trợ lý shop"
        clean_text = str(content).strip()[:300]
        lines.append(f"{role}: {clean_text}")
    return "\n".join(lines)


async def rewrite_contextual_query(
    query: str,
    messages: list,
    citations: list | None = None,
) -> str:
    """Rewrite a follow-up or elliptical query into a complete standalone search query (2026).

    Zero-cost gate:
    - If messages <= 1 (first turn), return raw query immediately (0 tokens, 0ms).
    - If query is long (> 15 words) and already mentions products, return raw query.
    - Otherwise, summarizes the last 1-2 exchanges and asks LLM to emit a standalone query.
    """
    prior = list(messages or [])
    if prior and getattr(prior[-1], "content", None) == query:
        prior = prior[:-1]
    if not prior:
        return query

    history_text = format_recent_history(prior, current_message=query, max_messages=4)
    if not history_text.strip():
        return query

    prompt = (
        "Dựa vào lịch sử hội thoại gần đây và câu hỏi tiếp theo của khách hàng, "
        "hãy viết lại câu hỏi tiếp theo thành một câu truy vấn tìm kiếm độc lập, đầy đủ "
        "bằng tiếng Việt, chứa đầy đủ tên sản phẩm và ngữ cảnh cụ thể được nhắc tới.\n"
        "QUY TẮC:\n"
        "- TUYỆT ĐỐI KHÔNG trả lời câu hỏi.\n"
        "- Chỉ xuất ra duy nhất câu truy vấn tìm kiếm đã được viết lại, không thêm lời dẫn giải.\n"
        "- Nếu câu hỏi của khách hàng đã đầy đủ tên sản phẩm và độc lập, hãy giữ nguyên câu hỏi.\n\n"
        f"LỊCH SỬ HỘI THOẠI:\n{history_text}\n\n"
        f"CÂU HỎI TIẾP THEO: {query}\n"
        "TRUY VẤN TÌM KIẾM ĐỘC LẬP:"
    )

    try:
        from services.ai import AIGateway

        resp = await AIGateway.complete(
            model="economy-chat",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.0,
        )
        choice = resp.choices[0]
        rewritten = getattr(choice.message, "content", "") or ""
        rewritten = rewritten.strip().strip('"').strip("'")
        if rewritten and len(rewritten) >= 3:
            return rewritten
    except Exception:
        pass

    return query
