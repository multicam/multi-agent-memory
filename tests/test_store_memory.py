"""Tests for the store_memory MCP tool (server.py).

These mock the external dependencies (PG, NAS, Embedder, Extractor)
to test the orchestration logic without real infrastructure.

The server module creates globals at import time (Config.from_env(), PGStorage,
etc.), so we set PG_URL before importing and then replace the globals with mocks.
"""

import os
import pytest
from unittest.mock import MagicMock, patch

# Set PG_URL before server.py is imported anywhere
os.environ.setdefault("PG_URL", "postgresql://mock:mock@localhost/test")

from src.extraction.facts import Extraction
import src.server as server_mod


@pytest.fixture(autouse=True)
def _patch_server_globals(monkeypatch):
    """Replace server module globals with mocks for every test in this file."""
    mock_pg = MagicMock()
    mock_pg.is_connected.return_value = True

    mock_jsonl = MagicMock()
    mock_jsonl.is_mounted.return_value = True

    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [0.1] * 768
    mock_embedder.model_name = "test-model"

    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = Extraction(
        facts=["fact1", "fact2"],
        decisions=["Decided X because Y"],
        entities=[{"name": "Redis", "type": "service"}],
        tags=["infrastructure"],
        shareable=True,
        model="test-model",
        extracted_at="2026-03-24T00:00:00+00:00",
        status="success",
    )

    monkeypatch.setattr(server_mod, "pg", mock_pg)
    monkeypatch.setattr(server_mod, "jsonl", mock_jsonl)
    monkeypatch.setattr(server_mod, "embedder", mock_embedder)
    monkeypatch.setattr(server_mod, "extractor", mock_extractor)

    # Expose mocks for tests that need direct access
    _patch_server_globals.pg = mock_pg
    _patch_server_globals.jsonl = mock_jsonl
    _patch_server_globals.embedder = mock_embedder
    _patch_server_globals.extractor = mock_extractor


def _pg():
    return _patch_server_globals.pg


def _jsonl():
    return _patch_server_globals.jsonl


def _embedder():
    return _patch_server_globals.embedder


def _extractor():
    return _patch_server_globals.extractor


@pytest.mark.integration
class TestStoreMemoryValidation:
    """specs/store-memory.md -- input validation scenarios."""

    def test_empty_text_rejected(self):
        """Empty text returns error dict, not a crash."""
        result = server_mod.store_memory("", "ag-1", "sess-1")
        assert "error" in result
        assert "empty" in result["error"].lower()

    def test_whitespace_only_text_rejected(self):
        """Whitespace-only text is treated as empty."""
        result = server_mod.store_memory("   ", "ag-1", "sess-1")
        assert "error" in result

    def test_empty_agent_id_rejected(self):
        """Empty agent_id returns error dict."""
        result = server_mod.store_memory("some text", "", "sess-1")
        assert "error" in result
        assert "agent_id" in result["error"]


@pytest.mark.integration
class TestStoreMemoryHappyPath:
    """specs/store-memory.md -- successful store scenarios."""

    def test_returns_expected_fields(self):
        """Successful store returns id, agent_id, session_id, created_at, etc."""
        result = server_mod.store_memory("Redis runs on port 6379", "ag-1", "sess-1")

        assert "id" in result
        assert result["agent_id"] == "ag-1"
        assert result["session_id"] == "sess-1"
        assert "created_at" in result
        assert "promoted" in result
        assert "extraction" in result
        assert "storage" in result

    def test_storage_status_ok(self):
        """Both JSONL and PG report ok on success."""
        result = server_mod.store_memory("test content", "ag-1", "sess-1")
        assert result["storage"]["jsonl"] == "ok"
        assert result["storage"]["pg"] == "ok"

    def test_extraction_summary_in_response(self):
        """Response contains extraction summary (counts, not full facts)."""
        result = server_mod.store_memory("test content", "ag-1", "sess-1")
        ext = result["extraction"]
        assert ext["facts"] == 2
        assert ext["decisions"] == 1
        assert ext["entities"] == 1
        assert ext["status"] == "success"


@pytest.mark.integration
class TestStoreMemoryWriteAhead:
    """specs/store-memory.md -- write-ahead guarantee."""

    def test_jsonl_before_pg(self):
        """JSONL append is called before PG store."""
        call_order = []
        _jsonl().append.side_effect = lambda **kw: call_order.append("jsonl")
        _pg().store.side_effect = lambda **kw: call_order.append("pg")

        server_mod.store_memory("test content", "ag-1", "sess-1")

        assert "jsonl" in call_order
        assert "pg" in call_order
        assert call_order.index("jsonl") < call_order.index("pg")

    def test_pg_failure_still_has_jsonl(self):
        """If PG fails, JSONL is still ok."""
        _pg().store.side_effect = Exception("PG down")

        result = server_mod.store_memory("test content", "ag-1", "sess-1")

        assert result["storage"]["jsonl"] == "ok"
        assert result["storage"]["pg"] == "failed"
        assert "error" not in result  # Not a total failure


@pytest.mark.integration
class TestStoreMemoryPromotion:
    """specs/store-memory.md -- promotion scenarios."""

    def test_promoted_memory_written_to_shared(self):
        """Shareable extraction triggers append_shared."""
        server_mod.store_memory("infra knowledge", "ag-1", "sess-1")
        _jsonl().append_shared.assert_called_once()

    def test_private_memory_not_shared(self):
        """Non-shareable extraction does NOT trigger append_shared."""
        _extractor().extract.return_value = Extraction(
            facts=["debugging note"],
            tags=["debugging", "wip"],
            shareable=False,
            model="test",
            extracted_at="2026-03-24T00:00:00+00:00",
        )

        server_mod.store_memory("debug note", "ag-1", "sess-1")
        _jsonl().append_shared.assert_not_called()


@pytest.mark.integration
class TestStoreMemoryFailure:
    """specs/store-memory.md -- failure scenarios."""

    def test_both_backends_fail(self):
        """Both JSONL and PG failing returns error."""
        _jsonl().append.side_effect = OSError("NAS down")
        _pg().store.side_effect = Exception("PG down")

        result = server_mod.store_memory("test", "ag-1", "sess-1")
        assert "error" in result
        assert "Both" in result["error"]

    def test_embedding_failure_is_non_fatal(self):
        """Embedding failure doesn't prevent storage."""
        _embedder().embed.side_effect = RuntimeError("Model not loaded")

        result = server_mod.store_memory("test content", "ag-1", "sess-1")
        # Should still succeed (embed returns None, stored without embedding)
        assert "error" not in result
        assert result["storage"]["jsonl"] == "ok"

    def test_facts_stored_as_semantic_rows(self):
        """Extracted facts are stored via pg.store_facts()."""
        server_mod.store_memory("Redis runs on port 6379", "ag-1", "sess-1")
        _pg().store_facts.assert_called_once()
