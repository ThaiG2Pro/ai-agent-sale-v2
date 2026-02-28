"""Algorithm constants for RAG pipeline."""

# ── Schema constant (mirrors models/schema.py SCHEMA) ─────────────────────────
SCHEMA = "agent_v1"

# ── Algorithm constants (FR-005, FR-012, FR-013) ──────────────────────────────
RRF_K: int = 60
# bge-m3 cosine similarity: related cross-lingual content typically scores 0.35-0.70
CONFIDENCE_THRESHOLD: float = 0.35
# Use lower threshold so RRF-boosted FTS hits are not discarded by vector score alone
COMPRESSION_SCORE_THRESHOLD: float = 0.25
NEAR_DUP_THRESHOLD: float = 0.80

DECLINE_MESSAGE = (
    "Tôi không tìm thấy thông tin liên quan đến câu hỏi của bạn. "
    "Vui lòng thử lại với từ khóa cụ thể hơn."
)

# Action verbs used for query classification (FR-015, Q2 clarification)
ACTION_VERBS: frozenset[str] = frozenset(
    [
        # English
        "price",
        "cost",
        "compare",
        "buy",
        "order",
        "discount",
        "ship",
        "warranty",
        "available",
        "feature",
        "quantity",
        # Vietnamese
        "giá",
        "mua",
        "đặt",
        "so sánh",
        "giao",
        "hoàn tiền",
        "cài đặt",
        "bảo hành",
        "có sẵn",
        "tính năng",
        "đặt hàng",
        "kho",
        "chiết khấu",
    ]
)

ANSWER_SYSTEM_PROMPT = (
    "You are a knowledgeable sales assistant for a Vietnamese SME business.\n"
    "Answer the customer's question thoroughly and accurately based ONLY on the "
    "provided product context.\n"
    "Include all relevant details from the context: specifications, features, price, "
    "availability — whatever is applicable to the question.\n"
    "Respond in the same language as the customer's question.\n"
    "If the context does not fully answer the question, say so honestly.\n"
    "Do NOT cut your answer short — give a complete, helpful response."
)
