"""Order mutator using Light Model + Structured Extraction (Pha 1).

Why: Eliminates brittle regex matches (e.g. SC3, NQ2, _MODIFY_PATTERNS) and protects
     order integrity by using a light model with a deterministic Pydantic schema.
"""

from __future__ import annotations

import logging
from typing import Any

from core.agent.schemas.mutation import FulfillmentDetails, OrderActionEnum, OrderMutationRequest
from services.ai import AIGateway

logger = logging.getLogger(__name__)

_MUTATION_SYSTEM_PROMPT = """Bạn là trợ lý AI phân tích hành vi của khách hàng đối với đơn hàng đang được xem xét (review/pending).
Nhiệm vụ của bạn là bóc tách chính xác Ý ĐỊNH và THỰC THỂ từ tin nhắn của khách hàng.

Các loại hành động (action):
1. confirm: Khách đồng ý, xác nhận đơn hàng, hối thúc duyệt đơn ("ok shop", "duyệt nhanh giùm", "chốt nhé", "bao lâu thì có", "nào xong báo tôi nhé").
2. cancel: Khách muốn hủy đơn, không mua nữa ("thôi hủy đi", "không lấy nữa", "cancel đơn").
3. update_quantity: Khách muốn đổi SỐ LƯỢNG của sản phẩm hiện tại ("đổi thành 1 con thôi đc không", "lấy 2 cái nhé", "cho mình 3 hộp").
   - Ghi nhận `new_quantity` là số nguyên tương ứng.
   - TUYỆT ĐỐI KHÔNG điền `target_product_name` khi khách chỉ thay đổi số lượng!
4. change_product: Khách muốn ĐỔI SANG SẢN PHẨM KHÁC ("đổi sang iPhone 15", "thay bằng bản Pro nhé", "lấy con màu đen thay cho con này").
   - Ghi nhận `target_product_name` là tên hoặc đặc điểm sản phẩm mới.
   - Nếu khách không nói rõ sản phẩm nào muốn đổi sang, để trống `target_product_name`.
5. add_on: Khách muốn MUA THÊM sản phẩm phụ kiện kèm theo đơn hiện tại ("thêm 1 sạc Anker vào đơn nhé", "lấy thêm ốp lưng nhé", "cũng lấy thêm tai nghe").
   - Ghi nhận `target_product_name` là tên sản phẩm muốn mua thêm.
   - Ghi nhận `new_quantity` là số lượng mua thêm (mặc định 1 nếu không nói).
6. negotiate_price: Khách mặc cả hoặc trả giá ("giảm bớt được không", "20 triệu bán không", "27.9tr được thì lấy không thì hủy").
   - Ghi nhận `proposed_price` nếu có mức giá cụ thể (ví dụ 27900000.0).
7. info_query: Khách đặt câu hỏi về sản phẩm, thông số, giao hàng, bảo hành ("máy này bao nhiêu Watt", "có bảo hành không", "giao trong bao lâu").
8. general_query: Tin nhắn chào hỏi hoặc trò chuyện vu vơ không làm thay đổi đơn ("shop ở đâu vậy", "hàng chính hãng không").

QUY TẮC BẤT DI BẤT DỊCH:
- Các câu nói như "đổi thành 1 con", "đổi thành 2 cái", "lấy 2 cái nhé", "cho mình 3 hộp" là `update_quantity`. Hãy trích xuất con số khách yêu cầu vào `new_quantity` (ví dụ: "lấy 2 cái nhé" -> `new_quantity=2`), KHÔNG PHẢI `change_product`.
- Chỉ chọn `change_product` khi khách muốn THAY THẾ sản phẩm cũ bằng một sản phẩm khác có tên cụ thể. Nếu khách muốn GIỮ sản phẩm cũ và LẤY THÊM sản phẩm mới, chọn `add_on`.
- Nếu khách vừa trả giá vừa dọa hủy ("27.9tr được thì lấy, không thì hủy"), ưu tiên `negotiate_price` và bóc tách `proposed_price`.
"""


async def parse_order_mutation(
    text: str,
    current_order: dict[str, Any] | None = None,
) -> OrderMutationRequest:
    """Parse customer message into a structured OrderMutationRequest using the light chat model."""
    cleaned = text.strip()
    if not cleaned:
        return OrderMutationRequest(
            action=OrderActionEnum.GENERAL_QUERY,
            reasoning="Empty text",
            confidence=1.0,
        )

    order_context = ""
    if current_order:
        p_name = (
            current_order.get("name")
            or current_order.get("product_name")
            or current_order.get("sku")
            or "chưa rõ"
        )
        qty = current_order.get("quantity", 1)
        order_context = (
            f"\nThông tin đơn hàng hiện tại của khách: Sản phẩm '{p_name}', số lượng: {qty}."
        )

    prompt = f'Tin nhắn của khách hàng: "{cleaned}"{order_context}\nHãy phân loại chính xác theo định dạng yêu cầu.'

    try:
        response = await AIGateway.complete(
            model="light-chat",
            messages=[
                {"role": "system", "content": _MUTATION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format=OrderMutationRequest,
            temperature=0.0,
            max_tokens=512,
        )

        message = response.choices[0].message
        content = getattr(message, "content", "")
        parsed = getattr(message, "parsed", None)

        if isinstance(content, str) and content.strip():
            result = OrderMutationRequest.model_validate_json(content)
        elif isinstance(parsed, OrderMutationRequest):
            result = parsed
        elif isinstance(content, OrderMutationRequest):
            result = content
        elif isinstance(content, dict):
            result = OrderMutationRequest(**content)
        else:
            return OrderMutationRequest(
                action=OrderActionEnum.GENERAL_QUERY,
                reasoning="Unparseable LLM response",
                confidence=0.5,
            )

        if result.action not in (OrderActionEnum.CHANGE_PRODUCT, OrderActionEnum.ADD_ON):
            result.target_product_name = None
        elif result.target_product_name is not None and not result.target_product_name.strip():
            result.target_product_name = None
        return result
    except Exception as exc:
        logger.warning(
            "Failed to parse order mutation via LLM for text '%s': %s; falling back to safe general query",
            cleaned[:50],
            exc,
        )
        # Safe fallback: never mutate order blindly on LLM error
        return OrderMutationRequest(
            action=OrderActionEnum.GENERAL_QUERY,
            reasoning=f"Parse fallback on error: {exc}",
            confidence=0.5,
        )


_FULFILLMENT_SYSTEM_PROMPT = """Bạn là trợ lý AI trích xuất thực thể thông tin đặt hàng của khách hàng (Fulfillment Entity Extractor).
Nhiệm vụ: Trích xuất chính xác 4 trường thông tin từ tin nhắn của khách hàng:
1. phone: Số điện thoại nhận hàng (chuẩn hóa 10 chữ số, ví dụ '0566464459'). Nếu không có số điện thoại hợp lệ thì để null.
2. address: Địa chỉ nhận hàng ĐẦY ĐỦ bao gồm số nhà, tên đường, phường/xã, quận/huyện, tỉnh/thành phố (ví dụ: '123 Lê Lợi, Quận 1, TP HCM', 'hồ chí minh', 'Hà Nội'). TUYỆT ĐỐI giữ nguyên vẹn toàn bộ chuỗi địa chỉ, không được lược bỏ số nhà hay tên đường! Nếu không nhắc đến thì để null.
3. quantity: Số lượng sản phẩm khách muốn đặt (số nguyên > 0, ví dụ: '1 con' -> 1, 'hai cái' -> 2, '3 máy' -> 3). Mặc định là 1 nếu khách xác nhận đặt mua. Nếu không nhắc thì để null.
4. target_budget: Mức ngân sách tiền VNĐ nếu khách có đề cập giới hạn (ví dụ: 'dưới 10 triệu' -> 10000000.0, 'tầm 15tr' -> 15000000.0). Nếu không có thì để null.

CHÚ Ý: Chỉ trích xuất thông tin có thực trong tin nhắn. Giữ nguyên đầy đủ chi tiết địa chỉ người dùng cung cấp.
"""


async def extract_fulfillment_details(text: str) -> FulfillmentDetails:
    """Extract delivery phone, address, quantity and budget using light model instead of brittle regex."""
    cleaned = (text or "").strip()
    if not cleaned:
        return FulfillmentDetails()

    try:
        response = await AIGateway.complete(
            model="light-chat",
            messages=[
                {"role": "system", "content": _FULFILLMENT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f'Trích xuất thông tin từ tin nhắn khách hàng: "{cleaned}"',
                },
            ],
            response_format=FulfillmentDetails,
            temperature=0.0,
            max_tokens=1024,
        )
        message = response.choices[0].message
        content = getattr(message, "content", "")
        parsed = getattr(message, "parsed", None)

        if isinstance(parsed, FulfillmentDetails):
            result = parsed
        elif isinstance(content, FulfillmentDetails):
            result = content
        elif isinstance(content, str) and content.strip():
            result = FulfillmentDetails.model_validate_json(content)
        elif isinstance(content, dict):
            result = FulfillmentDetails(**content)
        else:
            result = FulfillmentDetails()

        # Sanitize empty strings to None
        if result.phone and not result.phone.strip():
            result.phone = None
        if result.address and not result.address.strip():
            result.address = None
        return result
    except Exception as exc:
        logger.warning("extract_fulfillment_details failed for '%s': %s", cleaned[:40], exc)
        return FulfillmentDetails()
