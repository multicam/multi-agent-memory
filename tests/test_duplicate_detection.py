"""Tests for duplicate detection (specs/duplicate-detection.md).

Tests the check_duplicate method in PGStorage and the dedup integration
in store_memory. Uses mocked PG to avoid real database dependency.
"""

import os
import pytest
from unittest.mock import MagicMock

os.environ.setdefault("PG_URL", "postgresql://mock:mock@localhost/test")

from src.extraction.facts import Extraction
import src.server as server_mod


@pytest.fixture(autouse=True)
def _patch_server_globals(monkeypatch):
    """Replace server module globals with mocks."""
    mock_pg = MagicMock()
    mock_pg.is_connected.return_value = True
    mock_pg.check_duplicate.return_value = None  # no dup by default

    mock_jsonl = MagicMock()
    mock_jsonl.is_mounted.return_value = True

    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [0.1] * 768

    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = Extraction(
        facts=["fact1"],
        decisions=[],
        entities=[],
        tags=["test"],
        shareable=False,
        model="test",
        extracted_at="2026-04-09T00:00:00+00:00",
        status="success",
    )

    monkeypatch.setattr(server_mod, "pg", mock_pg)
    monkeypatch.setattr(server_mod, "jsonl", mock_jsonl)
    monkeypatch.setattr(server_mod, "embedder", mock_embedder)
    monkeypatch.setattr(server_mod, "extractor", mock_extractor)

    _patch_server_globals.pg = mock_pg
    _patch_server_globals.embedder = mock_embedder


def _pg():
    return _patch_server_globals.pg


def _embedder():
    return _patch_server_globals.embedder


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


@pytest.mark.unit
class TestCheckDuplicateMethod:
    """specs/duplicate-detection.md -- check_duplicate PG method."""

    def test_above_threshold_returns_id(self):
        """Similarity > 0.92 returns the existing memory ID."""
        from src.storage.postgres import PGStorage

        pg = PGStorage.__new__(PGStorage)
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {
            "id": "abc-123",
            "similarity": 0.95,
        }
        pg._conn = mock_conn
        pg._pool = None

        result = pg.check_duplicate([0.1] * 768, "ag-1", threshold=0.92)
        assert result == "abc-123"

    def test_below_threshold_returns_none(self):
        """Similarity <= 0.92 returns None."""
        from src.storage.postgres import PGStorage

        pg = PGStorage.__new__(PGStorage)
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {
            "id": "abc-123",
            "similarity": 0.85,
        }
        pg._conn = mock_conn
        pg._pool = None

        result = pg.check_duplicate([0.1] * 768, "ag-1", threshold=0.92)
        assert result is None

    def test_no_results_returns_none(self):
        """Empty table returns None."""
        from src.storage.postgres import PGStorage

        pg = PGStorage.__new__(PGStorage)
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        pg._conn = mock_conn
        pg._pool = None

        result = pg.check_duplicate([0.1] * 768, "ag-1")
        assert result is None
