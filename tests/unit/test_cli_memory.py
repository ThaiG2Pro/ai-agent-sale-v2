"""Tests for memory CLI commands (Phase 7, T130-T131)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner


@pytest.fixture
def cli_runner():
    """Fixture for Click CLI runner."""
    return CliRunner()


class TestReembedCommand:
    """Test suite for reembed-semantic-memory command."""

    def test_reembed_dry_run_logs_count_no_update(self, cli_runner):
        """T131: reembed dry-run → logs count, no rows updated."""
        # Patch imports before importing CLI
        with patch.dict("sys.modules", {"sqlalchemy": MagicMock(), "sqlalchemy.ext": MagicMock()}):
            with patch("cli.memory.get_db_session", new_callable=AsyncMock) as mock_get_db:
                from cli.memory import memory

                # Mock database with records
                mock_db = AsyncMock()
                mock_result = MagicMock()

                # Create mock semantic memory records
                mock_records = [MagicMock() for _ in range(5)]
                for record in mock_records:
                    record.id = "id_" + str(id(record))
                    record.status = "STALE"
                    record.embedding_model = "old-model"

                mock_result.scalars.return_value.all.return_value = mock_records
                mock_db.execute = AsyncMock(return_value=mock_result)
                mock_get_db.return_value = mock_db

                # Run dry-run
                result = cli_runner.invoke(
                    memory,
                    [
                        "reembed-semantic-memory",
                        "--dry-run",
                    ],
                )

                # Verify output mentions the count
                assert result.exit_code == 0
                assert "Found 5 records" in result.output
                assert "Dry-run mode" in result.output
                assert "Would update 5 records" in result.output

                # Verify no commit was called
                mock_db.commit.assert_not_called()

    def test_reembed_no_records_to_update(self, cli_runner):
        """Reembed with no STALE records → early exit."""
        with patch("cli.memory.get_db_session", new_callable=AsyncMock) as mock_get_db:
            from cli.memory import memory

            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []

            mock_db.execute = AsyncMock(return_value=mock_result)
            mock_get_db.return_value = mock_db

            result = cli_runner.invoke(
                memory,
                [
                    "reembed-semantic-memory",
                    "--dry-run",
                ],
            )

            assert result.exit_code == 0
            assert "No records need re-embedding" in result.output


class TestDeleteCustomerMemoryCommand:
    """Test suite for delete-customer-memory command."""

    def test_delete_requires_confirm_flag(self, cli_runner):
        """Delete without --confirm flag → shows warning, no deletion."""
        from cli.memory import memory

        result = cli_runner.invoke(
            memory,
            [
                "delete-customer-memory",
                "--customer-id",
                "cust_123",
            ],
        )

        assert result.exit_code == 0
        assert "Use --confirm flag" in result.output
        assert "DELETE all memory" in result.output

    def test_delete_with_confirm_flag(self, cli_runner):
        """Delete with --confirm → deletion proceeds."""
        with patch("cli.memory.get_db_session", new_callable=AsyncMock) as mock_get_db:
            from cli.memory import memory

            mock_db = AsyncMock()
            mock_db.commit = AsyncMock()
            mock_db.close = AsyncMock()

            # Mock execute to return empty result for counting
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_db.execute = AsyncMock(return_value=mock_result)

            mock_get_db.return_value = mock_db

            result = cli_runner.invoke(
                memory,
                [
                    "delete-customer-memory",
                    "--customer-id",
                    "cust_123",
                    "--confirm",
                ],
            )

            assert result.exit_code == 0
            assert "Deletion complete" in result.output
            # Verify commit was called
            mock_db.commit.assert_called()
