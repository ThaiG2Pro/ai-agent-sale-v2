"""Contract test fixtures for tool validation."""

import pytest
import respx


@pytest.fixture
def respx_mock():
    """Provide respx mock for HTTP transport-layer mocking."""
    with respx.mock:
        yield respx
