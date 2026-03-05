"""Root conftest for pytest plugin configuration."""

import pytest

pytest_plugins = ["pytest_asyncio"]


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: mark test as integration (requires DB, Ollama)",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip integration tests in fast CI mode."""
    # --collect-only flag (pytest uses 'collectonly', not 'collect_only')
    if config.option.collectonly:
        for item in items:
            if "integration" in item.nodeid:
                item.add_marker(
                    pytest.mark.skip(reason="Integration skipped in collect-only mode")
                )
