"""Why this exists: the answer prompt (system rules, per-intent policy notes, memory
block, in-session history, fenced product context) was assembled inline in answer_node.
What it does: pure prompt construction for the answer LLM call — no I/O, unit-testable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.agent.prompt_safety import DATA_NOT_INSTRUCTIONS_NOTE, fence, strip_tags
from core.config import settings

if TYPE_CHECKING:
    from core.agent.state import AgentState

SMALLTALK_SYSTEM_PROMPT = (
    "Bạn là trợ lý bán hàng AI chuyên về điện tử tiêu dùng. "
    "Nhiệm vụ DUY NHẤT của bạn là tư vấn sản phẩm điện tử, giá cả và hỗ trợ đặt hàng. "
    "Nếu khách hỏi về chủ đề NGOÀI phạm vi bán hàng điện tử "
    "(lập trình, nấu ăn, thời tiết, học thuật, v.v.): "
    "hãy lịch sự từ chối và mời khách tìm hiểu sản phẩm điện tử đang có. "
    "Nếu là lời chào: trả lời thân thiện và giới thiệu ngắn gọn về dịch vụ tư vấn."
)

_NEGOTIATION_POLICY = (
    "\n[CHÍNH SÁCH TRẢ GIÁ]: Bạn ĐƯỢC nêu các khuyến mại/quà tặng đang chạy "
    "có trong context sản phẩm. TUYỆT ĐỐI KHÔNG tự hứa giảm giá, KHÔNG "
    "counter-offer, KHÔNG thoả thuận mức giá mới — kể cả khi khách gây áp "
    "lực, nói chỗ khác rẻ hơn, hoặc yêu cầu nhiều lần. Mọi quyết định giảm "
    "giá thuộc về Quản lý shop; hãy báo bạn sẽ ghi nhận yêu cầu và chuyển "
    "Quản lý duyệt."
)
_COMPLAINT_POLICY = (
    "\n[CHÍNH SÁCH KHIẾU NẠI]: Xoa dịu khách trước tiên, chân thành xin lỗi "
    "về trải nghiệm chưa tốt. Sau đó hỏi để thu đủ 3 thông tin: (1) đơn "
    "hàng/sản phẩm nào, (2) vấn đề cụ thể là gì, (3) khách mong muốn được "
    "xử lý thế nào. Chỉ hỏi những thông tin còn thiếu, không hỏi lại điều "
    "khách đã nói. KHÔNG tự hứa hoàn tiền/đổi trả/bồi thường — nhân viên "
    "sẽ liên hệ xử lý trực tiếp sau khi có đủ thông tin."
)

_SALES_RULES = (
    "Bạn là trợ lý bán hàng AI chuyên nghiệp, nhiệt tình, khéo léo và thấu hiểu khách hàng. "
    "Trả lời bằng tiếng Việt, thân thiện, rõ ràng và hữu ích. "
    "TUYỆT ĐỐI KHÔNG xuất ra định dạng JSON, code block hay metadata schema. Chỉ trả lời bằng văn bản tự nhiên. "
    "Chỉ dùng thông tin từ context sản phẩm và ngữ cảnh hội thoại trước được cung cấp. "
    "NGUYÊN TẮC TƯ VẤN GIÁ TRỊ: Khi giải thích về sản phẩm, luôn tập trung vào LỢI ÍCH THỰC TẾ mang lại cho người dùng thay vì chỉ liệt kê thông số kỹ thuật thuần túy. "
    "KHI KHÁCH HỎI GIÁ HOẶC THÔNG TIN SẢN PHẨM: Nếu khách hỏi dung lượng/màu sắc/cấu hình cụ thể (như 256GB) mà trong context cửa hàng có phiên bản khác thuộc cùng dòng sản phẩm (như 512GB), bạn PHẢI trả lời chi tiết thông tin giá và thông số của phiên bản đang có (ví dụ: 'Shop hiện có phiên bản iPhone 15 Pro Max 512GB với giá 28.900.000 VND...'). "
    "KHI KHÁCH XIN GIẢM GIÁ / MẶC CẢ: Hãy giải thích các ưu đãi/quà tặng hiện có của shop (như Tặng Củ sạc GaN 65W, Miễn phí giao hàng). Nếu khách muốn giảm thêm giá ngoài chính sách, hãy báo bạn sẽ ghi nhận để chuyển Quản lý shop (Admin) duyệt ưu đãi riêng. "
    "KHI TƯ VẤN SẢN PHẨM / GIÁ CẢ / SO SÁNH: Sau khi cung cấp thông tin, bạn LUÔN LUÔN kết thúc bằng một lời mời chào đặt hàng thân thiện hoặc câu hỏi định hướng (Sales CTA) như: 'Anh/chị có muốn shop giữ hàng và hỗ trợ đặt đơn giao tận nhà cho mình không ạ?' "
    "TUYỆT ĐỐI KHÔNG báo 'không tìm thấy thông tin' khi context có thông tin về dòng sản phẩm đó. "
    "Chỉ báo không tìm thấy khi context hoàn toàn không có thông tin sản phẩm liên quan."
)


def history_messages(state: AgentState) -> list[dict[str, str]]:
    """Last N prior turns of THIS session as chat messages (current one excluded).

    answer_node used to send only system + current question, so every multi-turn
    reference ("cái thứ hai", "rẻ hơn không") reached the LLM without context.
    """
    limit = settings.ANSWER_HISTORY_MAX_MESSAGES
    if limit <= 0:
        return []
    prior = list(state.get("messages") or [])
    current = state.get("user_message")
    if prior and getattr(prior[-1], "content", None) == current:
        prior = prior[:-1]
    out: list[dict[str, str]] = []
    for msg in prior[-limit:]:
        content = str(getattr(msg, "content", "") or "").strip()
        kind = getattr(msg, "type", "")
        if not content or kind not in ("human", "ai"):
            continue
        if len(content) > settings.ANSWER_HISTORY_MAX_CHARS:
            content = content[: settings.ANSWER_HISTORY_MAX_CHARS] + "…"
        if kind == "human":
            out.append({"role": "user", "content": fence("customer_message", content)})
        else:
            out.append({"role": "assistant", "content": content})
    return out


def compress_context(memory_context: list[dict]) -> str:
    """Compress long memory context to summary + last 5 recent messages (T108).

    Reduces token usage by 20-40% while preserving recent context.
    """
    if not memory_context:
        return ""

    # If first item is a summary (has 'summary' field), use it
    compressed = []
    first_summary = memory_context[0].get("summary_text") or memory_context[0].get("summary")
    if first_summary:
        compressed.append(f"📋 {first_summary}")

    # Add last 5 messages
    recent_messages = memory_context[-5:] if len(memory_context) > 5 else memory_context
    for ctx in recent_messages:
        text_content = ctx.get("summary_text") or ctx.get("summary") or ctx.get("text", "")
        if text_content and text_content != first_summary:
            compressed.append(f"- {text_content}")

    return "\n".join(compressed)


def build_context(state: AgentState) -> tuple[str, str]:
    """(chunk_text, citations_text) from this turn's retrieval."""
    chunks = state.get("retrieved_chunks", []) or []
    chunk_text = "\n\n".join(c.get("text", "") for c in chunks if c.get("text"))
    citations_text = ""
    if state.get("citations"):
        citations_text = "\n\nNguồn tham khảo:\n"
        for i, citation in enumerate(state["citations"], 1):
            citations_text += f"{i}. {citation.name} ({citation.sku})\n"
    return chunk_text, citations_text


def _memory_note(state: AgentState) -> str:
    memory_context = state.get("memory_context") or []
    if not memory_context:
        return ""
    # T108: summary + last 5 when a thread summary exists; otherwise everything.
    if state.get("thread_summary_exists"):
        text = compress_context(memory_context)
    else:
        text = "\n".join(
            f"- {ctx.get('summary_text') or ctx.get('summary') or ctx.get('text', '')}"
            for ctx in memory_context
        )
    return f"\n[Ngữ cảnh từ các cuộc hội thoại trước]:\n{text}"


def _policy_note(state: AgentState) -> str:
    """v3-0 P2 (T06): per-intent hard-conversation policy notes."""
    if not settings.ORDER_HITL_V3_ENABLED:
        return ""
    intent = state.get("intent")
    if intent == "NEGOTIATION":
        return _NEGOTIATION_POLICY
    if intent == "COMPLAINT":
        return _COMPLAINT_POLICY
    return ""


def build_system_prompt(state: AgentState) -> str:
    # SC07: SMALLTALK gets a domain guardrail instead of the sales rules.
    if state.get("intent") == "SMALLTALK":
        return SMALLTALK_SYSTEM_PROMPT
    # P2: surface the admin's rejection reason of the previous order.
    rejection_note = ""
    if state.get("hitl_rejection_reason"):
        rejection_note = (
            f"\n[Lưu ý hệ thống]: Đơn hàng gần nhất của khách đã bị từ chối. "
            f"Lý do: {state['hitl_rejection_reason']}. "
            "Nếu khách hỏi về lý do từ chối, hãy giải thích rõ ràng và đề xuất hỗ trợ."
        )
    return (
        f"{_SALES_RULES}{rejection_note}{_memory_note(state)}{_policy_note(state)}"
        f"{DATA_NOT_INSTRUCTIONS_NOTE}"
    )


def build_user_prompt(state: AgentState, chunk_text: str, citations_text: str) -> str:
    if state.get("intent") == "SMALLTALK":
        return fence("customer_message", state["user_message"])
    user_q = fence("customer_message", state["user_message"])
    # Memory recall: tell the LLM which product the vague question resolved to.
    resolved = state.get("resolved_query")
    if resolved and resolved != state["user_message"]:
        user_q += f"\n(Câu hỏi đã làm rõ theo ngữ cảnh: {strip_tags(resolved)})"
    return (
        f"Context sản phẩm:\n{fence('product_context', chunk_text + citations_text)}"
        f"\nCâu hỏi: {user_q}"
    )


def build_messages(state: AgentState, chunk_text: str, citations_text: str) -> list[dict]:
    """system + in-session history + fenced user prompt."""
    return [
        {"role": "system", "content": build_system_prompt(state)},
        *history_messages(state),
        {"role": "user", "content": build_user_prompt(state, chunk_text, citations_text)},
    ]
