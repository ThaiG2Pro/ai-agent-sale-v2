"""Why this exists: v3-0 P4 (T11 4.3) makes semantic-cache invalidation on
stock change mandatory — POST /admin/rag/restock changes stock, so it must
drop cached availability answers (and skip the delete when nothing changed).
What it does: Calls the route handler directly with a mocked session.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.routes.admin import RestockRequest, admin_restock


def _db(rowcount: int) -> AsyncMock:
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(rowcount=rowcount)
    return db


class TestAdminRestock:
    @pytest.mark.asyncio
    async def test_restock_invalidates_semantic_cache(self):
        db = _db(rowcount=3)
        with patch(
            "api.routes.admin.invalidate_cache", new=AsyncMock(return_value=7)
        ) as invalidate:
            out = await admin_restock(RestockRequest(min_stock=50), db)

        invalidate.assert_awaited_once_with(db)
        assert out == {"restocked": 3, "min_stock": 50, "cache_invalidated": 7}

    @pytest.mark.asyncio
    async def test_noop_restock_keeps_cache(self):
        db = _db(rowcount=0)
        with patch("api.routes.admin.invalidate_cache", new=AsyncMock()) as invalidate:
            out = await admin_restock(RestockRequest(min_stock=50), db)

        invalidate.assert_not_awaited()
        assert out["cache_invalidated"] == 0
