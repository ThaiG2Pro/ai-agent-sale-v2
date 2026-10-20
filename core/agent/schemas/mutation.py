"""Order mutation schemas for structured entity-gated operations."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class OrderActionEnum(StrEnum):
    """Specific order mutation action requested by customer."""

    CONFIRM = "confirm"  # Giục duyệt, đồng ý, xác nhận đơn
    CANCEL = "cancel"  # Hủy đơn hàng, không mua nữa
    UPDATE_QUANTITY = "update_quantity"  # Đổi/tăng/giảm số lượng (cùng sản phẩm hiện tại)
    CHANGE_PRODUCT = "change_product"  # Đổi sang dòng/loại sản phẩm khác hoàn toàn
    ADD_ON = "add_on"  # Mua thêm/kèm sản phẩm phụ kiện vào đơn hiện tại
    NEGOTIATE_PRICE = "negotiate_price"  # Trả giá, mặc cả giá
    INFO_QUERY = "info_query"  # Hỏi thông tin sản phẩm, bảo hành, giao hàng, câu hỏi kỹ thuật
    GENERAL_QUERY = "general_query"  # Hỏi thông tin phụ, chào hỏi vu vơ


class OrderMutationRequest(BaseModel):
    """Parsed structured intent and entity for order mutations."""

    action: OrderActionEnum = Field(
        ...,
        description="Loại hành động chính của khách đối với đơn hàng.",
    )
    new_quantity: int | None = Field(
        None,
        description="Số lượng mới nếu khách yêu cầu đổi số lượng (ví dụ: 1, 2, 3...). Bỏ trống nếu không đổi số lượng.",
    )
    target_product_name: str | None = Field(
        None,
        description="Tên hoặc đặc điểm sản phẩm mới NẾU KHÁCH NÊU RÕ khi muốn đổi mặt hàng (ví dụ: 'iPhone 15', 'ốp lưng đen'). TUYỆT ĐỐI BỎ TRỐNG nếu khách chỉ đổi số lượng ('1 con thôi', '2 cái') hoặc không nêu tên sản phẩm mới.",
    )
    proposed_price: float | None = Field(
        None,
        description="Mức giá đề xuất nếu khách đang mặc cả/thương lượng giá.",
    )
    confidence: float = Field(
        default=1.0,
        description="Độ tự tin phân loại từ 0.0 đến 1.0.",
    )


class FulfillmentDetails(BaseModel):
    """Customer fulfillment details extracted via Light Model."""

    phone: str | None = Field(
        None,
        description="Số điện thoại của khách hàng (chuẩn hóa 10 chữ số). Bỏ trống nếu không có.",
    )
    address: str | None = Field(
        None,
        description="Địa chỉ nhận hàng hoặc địa điểm/tỉnh thành của khách (ví dụ: 'hồ chí minh', '123 Lê Lợi, Q1'). Bỏ trống nếu không có.",
    )
    quantity: int | None = Field(
        None,
        description="Số lượng sản phẩm khách muốn mua (ví dụ: 1, 2, 3...). Bỏ trống nếu không nhắc đến.",
    )
    target_budget: float | None = Field(
        None,
        description="Mức ngân sách tối đa bằng số nguyên VNĐ nếu khách đề cập (ví dụ: 'dưới 15 triệu' -> 15000000.0).",
    )
