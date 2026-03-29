"""Unit tests for post-turn background tasks.

Tests cover:
- Task orchestration (post_turn_tasks coordinator)
- Parallel execution (asyncio.gather with return_exceptions)
- Individual task helpers (intent extraction, summarization, memory update, checkpoint sizing)
- Failure isolation (one task failure doesn't block others)
- TTFT budget (response before background completion)
"""


class TestPostTurnTasks:
    """Test suite for post-turn background task orchestration."""

    pass
