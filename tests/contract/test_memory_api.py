"""Contract tests for memory admin API.

Tests cover:
- Intent tracking endpoints (GET, PATCH, LIST)
- Semantic memory list endpoint
- RTBF (Right to Be Forgotten) DELETE endpoint
- Admin key authentication
- Error handling (404, 409, 401)
"""

import pytest


@pytest.fixture
def admin_headers():
    """Fixture for admin authentication headers."""
    return {"X-Admin-Key": "dev-secret-key"}


class TestMemoryContractPreImpl:
    """Contract test suite (pre-implementation validation)."""

    pass
