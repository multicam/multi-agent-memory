"""Tests for BM25 recall method (mocked PostgreSQL)."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from src.storage.postgres import PGStorage


@pytest.fixture
def pg():
    """PGStorage with mocked connection."""
    storage = PGStorage("postgresql://mock:mock@localhost/test")
    storage._conn = MagicMock()
    return storage


def _make_row(id: str, content: str, bm25_rank: float = 0.5, agent_id: str = "ag-1", shared: bool = False):
    """Create a mock DB row dict."""
    return {
        "id": id,
        "agent_id": agent_id,
        "memory_type": "episodic",
        "content": content,
        "source_session": "test-session",
        "shared": shared,
        "shared_by": agent_id if shared else None,
        "bm25_rank": bm25_rank,
        "created_at": datetime(2026, 3, 24, tzinfo=timezone.utc),
    }


def test_recall_bm25_returns_results(pg):
    """BM25 recall returns formatted results."""
    pg._conn.execute.return_value.fetchall.return_value = [
        _make_row("abc", "nginx config on port 80", bm25_rank=0.75),
    ]
    results = pg.recall_bm25("nginx port", "ag-1", limit=5)

    assert len(results) == 1
    assert results[0]["id"] == "abc"
    assert results[0]["bm25_rank"] == 0.75
    assert results[0]["content"] == "nginx config on port 80"


def test_recall_bm25_empty_results(pg):
    """BM25 recall returns empty list when no matches."""
    pg._conn.execute.return_value.fetchall.return_value = []
    results = pg.recall_bm25("nonexistent query", "ag-1")
    assert results == []


def test_recall_bm25_uses_plainto_tsquery(pg):
    """BM25 uses plainto_tsquery for safe natural language input."""
    pg._conn.execute.return_value.fetchall.return_value = []
    pg.recall_bm25("user's query with special chars", "ag-1")

    call_args = pg._conn.execute.call_args
    sql = call_args[0][0]
    assert "plainto_tsquery" in sql
    assert "to_tsquery" not in sql.replace("plainto_tsquery", "")


def test_recall_bm25_visibility_includes_shared(pg):
    """BM25 query includes shared memories."""
    pg._conn.execute.return_value.fetchall.return_value = []
    pg.recall_bm25("query", "ag-1")

    sql = pg._conn.execute.call_args[0][0]
    assert "shared = TRUE" in sql


def test_recall_bm25_not_connected():
    """BM25 raises RuntimeError when not connected."""
    pg = PGStorage("postgresql://mock:mock@localhost/test")
    with pytest.raises(RuntimeError, match="Not connected"):
        pg.recall_bm25("query", "ag-1")


def test_recall_semantic_returns_results(pg):
    """Semantic recall returns formatted results with similarity."""
    pg._conn.execute.return_value.fetchall.return_value = [
        {
            "id": "def",
            "agent_id": "ag-1",
            "memory_type": "semantic",
            "content": "JM prefers fd over find",
            "source_session": "test",
            "shared": True,
            "shared_by": "ag-1",
            "similarity": 0.85,
            "created_at": datetime(2026, 3, 24, tzinfo=timezone.utc),
        }
    ]
    results = pg.recall_semantic([0.1] * 768, "ag-2", limit=5)

    assert len(results) == 1
    assert results[0]["similarity"] == 0.85
    assert results[0]["shared"] is True


def test_recall_recency_fallback(pg):
    """Recency fallback returns results ordered by time."""
    pg._conn.execute.return_value.fetchall.return_value = [
        {
            "id": "old",
            "agent_id": "ag-1",
            "memory_type": "episodic",
            "content": "old memory",
            "source_session": "s1",
            "shared": False,
            "shared_by": None,
            "created_at": datetime(2026, 3, 20, tzinfo=timezone.utc),
        }
    ]
    results = pg.recall("anything", "ag-1", limit=5)
    assert len(results) == 1
    assert results[0]["id"] == "old"
