"""Contract test fixtures for tool validation."""

import pytest
import respx

pytest_plugins = ["pytest_asyncio"]


@pytest.fixture
def respx_mock():
    """Provide respx mock for HTTP transport-layer mocking."""
    with respx.mock:
        yield respx


@pytest.fixture(scope="session")
def asyncio_mode():
    """Enable async test mode."""
    return "auto"
