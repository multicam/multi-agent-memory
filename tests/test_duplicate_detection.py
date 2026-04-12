"""Tests for duplicate detection (specs/duplicate-detection.md).

Tests the check_duplicate method in PGStorage and the dedup integration
in store_memory. Uses mocked PG to avoid real database dependency.
"""

import pytest
from unittest.mock import MagicMock

import src.server as server_mod


@pytest.fixture(autouse=True)
def _use_server_mocks(server_mocks):
    """Wire up shared server mocks for all tests in this file."""
    _use_server_mocks.mocks = server_mocks


def _pg():
    return _use_server_mocks.mocks.pg


def _embedder():
    return _use_server_mocks.mocks.embedder


@pytest.mark.integration
class TestDuplicateDetection:
    """specs/duplicate-detection.md -- dedup scenarios."""

    def test_duplicate_detected_returns_early(self):
        """Near-duplicate returns status=duplicate with existing_id."""
        _pg().check_duplicate.return_value = "existing-uuid-123"

        result = server_mod.store_memory("Redis runs on 6379", "ag-1", "sess-1")

        assert result["status"] == "duplicate"
        assert result["existing_id"] == "existing-uuid-123"
        # Should NOT have called store
        _pg().store.assert_not_called()

    def test_no_duplicate_proceeds_normally(self):
        """Below threshold proceeds with normal storage."""
        _pg().check_duplicate.return_value = None

        result = server_mod.store_memory("unique content", "ag-1", "sess-1")

        assert "id" in result
        assert "status" not in result or result.get("status") != "duplicate"
        _pg().store.assert_called_once()

    def test_pg_failure_skips_dedup(self):
        """PG exception during dedup check is swallowed, storage continues."""
        _pg().check_duplicate.side_effect = Exception("PG timeout")

        result = server_mod.store_memory("some text", "ag-1", "sess-1")

        assert "id" in result
        assert "error" not in result

    def test_no_embedding_skips_dedup(self):
        """When embedding is None, dedup check is skipped entirely."""
        _embedder().embed.side_effect = RuntimeError("Model not loaded")

        result = server_mod.store_memory("some text", "ag-1", "sess-1")

        _pg().check_duplicate.assert_not_called()
        assert "error" not in result


def _pg_with_fetchone(fetchone_value):
    """Build a PGStorage with a mocked connection returning fetchone_value."""
    from src.storage.postgres import PGStorage

    pg = PGStorage.__new__(PGStorage)
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = fetchone_value
    pg._conn = mock_conn
    pg._pool = None
    return pg


@pytest.mark.unit
class TestCheckDuplicateMethod:
    """specs/duplicate-detection.md -- check_duplicate PG method."""

    def test_above_threshold_returns_id(self):
        """Similarity > 0.92 returns the existing memory ID."""
        pg = _pg_with_fetchone({"id": "abc-123", "similarity": 0.95})
        result = pg.check_duplicate([0.1] * 768, "ag-1", threshold=0.92)
        assert result == "abc-123"

    def test_below_threshold_returns_none(self):
        """Similarity <= 0.92 returns None."""
        pg = _pg_with_fetchone({"id": "abc-123", "similarity": 0.85})
        result = pg.check_duplicate([0.1] * 768, "ag-1", threshold=0.92)
        assert result is None

    def test_no_results_returns_none(self):
        """Empty table returns None."""
        pg = _pg_with_fetchone(None)
        result = pg.check_duplicate([0.1] * 768, "ag-1")
        assert result is None
