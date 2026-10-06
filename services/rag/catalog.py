"""Why this exists: the declined-browse fallback ("shop có gì?", "có laptop không") used
hardcoded brand regexes and raw SQL with hardcoded model names (`%swift%`, `%xps%`) —
every new product line needed a code change, and unknown categories fell through to an
alphabetical list of 12 random products.
What it does: builds the catalog fallback from the DATA. Categories come from the SKU
prefix of products actually in the DB (LAPTOP-…, PHONE-…); the only vocabulary kept in
code is the closed synonym list mapping a category to how customers name it. Brand /
model words are matched against real product names (parameterized ILIKE, R-SEC-003).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select

from models.schema import Product

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# SKU prefix → (display name, words customers use). A prefix not listed here still
# works: its display name and only synonym are the prefix itself.
CATEGORY_SYNONYMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "LAPTOP": ("Laptop", ("laptop", "lap", "máy tính xách tay", "notebook", "macbook")),
    "PHONE": ("Điện thoại", ("điện thoại", "phone", "smartphone", "đt", "dt", "iphone")),
    "TABLET": ("Máy tính bảng", ("máy tính bảng", "tablet", "ipad")),
    "MONITOR": ("Màn hình", ("màn hình", "monitor")),
    "WATCH": ("Đồng hồ thông minh", ("đồng hồ", "smartwatch", "watch")),
    "HEADPHONE": ("Tai nghe", ("tai nghe", "headphone", "earbuds", "airpods")),
    "KEYBOARD": ("Bàn phím", ("bàn phím", "keyboard")),
    "MOUSE": ("Chuột", ("chuột", "mouse")),
    "CHARGER": ("Sạc", ("sạc", "củ sạc", "charger", "sạc dự phòng")),
    "SSD": ("Ổ cứng SSD", ("ssd", "ổ cứng")),
}

# Words that never identify a product (so they are not matched against names).
_STOPWORDS = frozenset(
    "shop có không bán những gì nào cho xem tôi mình em anh chị bạn với và hay của các "
    "loại mẫu sản phẩm hàng giá bao nhiêu còn đang muốn mua tìm thì là ạ nhé nha ơi "
    "what do you have any the a show me list".split()
)
_TOKEN_RE = re.compile(r"[\wÀ-ỹ]+", re.UNICODE)
_LIST_LIMIT = 12
_MAX_QUERY_WORDS = 8


def _display(prefix: str) -> str:
    return CATEGORY_SYNONYMS.get(prefix, (prefix.title(), ()))[0]


def _match_category(query_l: str, prefixes: list[str]) -> str | None:
    """Category prefix (present in the DB) the query names, longest synonym first."""
    best: tuple[int, str] | None = None
    for prefix in prefixes:
        synonyms = CATEGORY_SYNONYMS.get(prefix, ("", ()))[1] or (prefix.lower(),)
        for syn in synonyms:
            if re.search(rf"(?<!\w){re.escape(syn)}(?!\w)", query_l) and (
                best is None or len(syn) > best[0]
            ):
                best = (len(syn), prefix)
    return best[1] if best else None


def _name_tokens(query_l: str, category: str | None) -> list[str]:
    """Query words that could be a brand/model ("dell", "galaxy", "s24")."""
    skip = set(_STOPWORDS)
    if category:
        for syn in CATEGORY_SYNONYMS.get(category, ("", ()))[1]:
            skip.update(syn.split())
    return [t for t in _TOKEN_RE.findall(query_l) if len(t) >= 2 and t not in skip]


async def build_catalog_response(query: str, db: AsyncSession) -> str | None:
    """Catalog listing for a vague/declined browse query, or None (→ decline).

    - query names a category in stock      → that category (narrowed by brand words)
    - query names brands/models in stock   → those products
    - query names something not in stock   → None (honest decline, no random list)
    - query names nothing                  → category overview with counts
    """
    query_l = (query or "").lower()
    if len(query_l.split()) > _MAX_QUERY_WORDS:
        return None

    prefix_col = func.split_part(Product.sku, "-", 1)
    counts = {
        row[0]: row[1]
        for row in (await db.execute(select(prefix_col, func.count()).group_by(prefix_col))).all()
        if row[0]
    }
    if not counts:
        return None

    category = _match_category(query_l, list(counts))
    tokens = _name_tokens(query_l, category)

    stmt = select(Product.name, Product.sku).order_by(Product.name).limit(_LIST_LIMIT)
    if category:
        stmt = stmt.where(Product.sku.ilike(f"{category}-%"))
    name_filter = or_(*(Product.name.ilike(f"%{t}%") for t in tokens)) if tokens else None

    rows: list = []
    if name_filter is not None:
        rows = list((await db.execute(stmt.where(name_filter))).all())
        if not rows and not category:
            # Product-ish words that match nothing we sell → honest decline.
            return None
    if not rows and category:
        rows = list((await db.execute(stmt)).all())

    if rows:
        header = (
            f"**Các mẫu {_display(category)} hiện có tại shop:**\n"
            if category
            else "**Sản phẩm phù hợp hiện có tại shop:**\n"
        )
        lines = [header, *(f"• {name} ({sku})" for name, sku in rows)]
        lines.append(
            "\nBạn quan tâm đến sản phẩm nào? Hãy hỏi thêm để biết thông tin chi tiết! 😊"
        )
        return "\n".join(lines)

    # Nothing specific asked → overview of what the shop actually stocks.
    overview = [
        f"• {_display(p)} ({n} mẫu)"
        for p, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return "\n".join(
        [
            "**Shop hiện có các nhóm sản phẩm:**\n",
            *overview,
            "\nAnh/chị quan tâm nhóm nào để em gửi danh sách chi tiết ạ? 😊",
        ]
    )
