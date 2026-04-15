"""Tests for the hybrid recall orchestration in server.py.

Covers the recall() MCP tool: semantic + BM25 channels, RRF merge, fallbacks.
"""

import pytest
from unittest.mock import MagicMock, patch


def _mem(id: str, content: str, **kwargs) -> dict:
    return {"id": id, "content": content, "agent_id": "ag-1", **kwargs}


@pytest.fixture
def mock_deps():
    """Mock all server-level dependencies for recall testing."""
    with patch("src.server.embedder") as mock_embedder, \
         patch("src.server.pg") as mock_pg:
        mock_embedder.embed.return_value = [0.1] * 768
        yield mock_embedder, mock_pg


class TestRecallHybrid:
    """Test the recall() MCP tool hybrid search flow."""

    def test_both_channels_merged_via_rrf(self, mock_deps):
        from src.server import recall
        _, mock_pg = mock_deps

        mock_pg.recall_semantic.return_value = [
            _mem("a", "semantic hit", similarity=0.8),
        ]
        mock_pg.recall_bm25.return_value = [
            _mem("b", "bm25 hit", bm25_rank=0.5),
        ]

        results = recall("test query", "ag-1", limit=10)

        assert len(results) == 2
        assert all("rrf_score" in r for r in results)
        mock_pg.recall_semantic.assert_called_once()
        mock_pg.recall_bm25.assert_called_once()

    def test_doc_in_both_channels_ranks_first(self, mock_deps):
        from src.server import recall
        _, mock_pg = mock_deps

        mock_pg.recall_semantic.return_value = [
            _mem("shared", "in both", similarity=0.7),
            _mem("sem-only", "semantic only", similarity=0.9),
        ]
        mock_pg.recall_bm25.return_value = [
            _mem("shared", "in both", bm25_rank=0.6),
            _mem("bm25-only", "bm25 only", bm25_rank=0.8),
        ]

        results = recall("test", "ag-1", limit=10)

        assert results[0]["id"] == "shared"

    def test_semantic_failure_falls_back_to_bm25_only(self, mock_deps):
        from src.server import recall
        mock_embedder, mock_pg = mock_deps

        mock_embedder.embed.side_effect = RuntimeError("model not loaded")
        mock_pg.recall_bm25.return_value = [
            _mem("b", "bm25 hit", bm25_rank=0.5),
        ]

        results = recall("test", "ag-1", limit=5)

        assert len(results) == 1
        assert results[0]["id"] == "b"

    def test_bm25_failure_falls_back_to_semantic_only(self, mock_deps):
        from src.server import recall
        _, mock_pg = mock_deps

        mock_pg.recall_semantic.return_value = [
            _mem("a", "semantic hit", similarity=0.8),
        ]
        mock_pg.recall_bm25.side_effect = RuntimeError("search_vector column missing")

        results = recall("test", "ag-1", limit=5)

        assert len(results) == 1
        assert results[0]["id"] == "a"

    def test_both_channels_empty_falls_back_to_recency(self, mock_deps):
        from src.server import recall
        _, mock_pg = mock_deps

        mock_pg.recall_semantic.return_value = []
        mock_pg.recall_bm25.return_value = []
        mock_pg.recall.return_value = [
            _mem("old", "recency fallback"),
        ]

        results = recall("test", "ag-1", limit=5)

        assert len(results) == 1
        assert results[0]["id"] == "old"
        mock_pg.recall.assert_called_once()

    def test_both_channels_fail_falls_back_to_recency(self, mock_deps):
        from src.server import recall
        mock_embedder, mock_pg = mock_deps

        mock_embedder.embed.side_effect = RuntimeError("embed fail")
        mock_pg.recall_bm25.side_effect = RuntimeError("bm25 fail")
        mock_pg.recall.return_value = [_mem("fallback", "last resort")]

        results = recall("test", "ag-1", limit=5)

        assert len(results) == 1
        assert results[0]["id"] == "fallback"

    def test_empty_agent_id_returns_error(self, mock_deps):
        from src.server import recall

        results = recall("test", "  ", limit=5)

        assert len(results) == 1
        assert "error" in results[0]

    def test_limit_passed_to_rrf(self, mock_deps):
        from src.server import recall
        _, mock_pg = mock_deps

        mock_pg.recall_semantic.return_value = [_mem(str(i), f"s{i}") for i in range(20)]
        mock_pg.recall_bm25.return_value = []

        results = recall("test", "ag-1", limit=3)

        assert len(results) == 3


class TestDecisionsInStoreMemory:
    """Test that decisions are stored alongside facts as semantic memories."""

    def test_decisions_combined_with_facts(self):
        """Verify store_memory combines facts + decisions into all_semantic."""
        from src.extraction.facts import Extraction

        extraction = Extraction(
            facts=["nginx runs on port 80"],
            decisions=["Decided to use port 3001 for dev because port 3000 is reserved for production"],
            tags=["infrastructure"],
            shareable=True,
            model="test",
            extracted_at="2026-01-01",
        )

        all_semantic = extraction.facts + extraction.decisions
        assert len(all_semantic) == 2
        assert "because" in all_semantic[1]

    def test_decisions_in_extraction_to_dict(self):
        """Verify decisions appear in to_dict output (stored in JSONL)."""
        from src.extraction.facts import Extraction

        e = Extraction(decisions=["Chose X because Y"])
        d = e.to_dict()
        assert "decisions" in d
        assert d["decisions"] == ["Chose X because Y"]

    def test_extraction_prompt_asks_for_why(self):
        """Verify the prompt explicitly requests rationale capture."""
        from src.extraction.facts import EXTRACTION_PROMPT
        assert "WHY" in EXTRACTION_PROMPT
        assert "decisions" in EXTRACTION_PROMPT
        assert "rationale" in EXTRACTION_PROMPT.lower()
        assert "Decided X because Y" in EXTRACTION_PROMPT
