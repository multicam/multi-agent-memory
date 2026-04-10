"""Tests for verbatim chunking (specs/chunking.md).

Tests the _chunk_text helper and the chunking integration in store_memory.
"""

import os
import pytest
from unittest.mock import MagicMock, call

os.environ.setdefault("PG_URL", "postgresql://mock:mock@localhost/test")

from src.extraction.facts import Extraction
import src.server as server_mod


def _extraction(**kwargs) -> Extraction:
    defaults = dict(
        facts=[], decisions=[], entities=[], tags=["test"],
        shareable=False, model="test", extracted_at="2026-04-09T00:00:00+00:00",
        status="success",
    )
    defaults.update(kwargs)
    return Extraction(**defaults)


@pytest.fixture(autouse=True)
def _patch_server_globals(monkeypatch):
    """Replace server module globals with mocks."""
    mock_pg = MagicMock()
    mock_pg.is_connected.return_value = True
    mock_pg.check_duplicate.return_value = None

    mock_jsonl = MagicMock()
    mock_jsonl.is_mounted.return_value = True

    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [0.1] * 768

    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = _extraction()

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


@pytest.mark.unit
class TestChunkText:
    """specs/chunking.md -- chunk boundary math."""

    def test_short_text_single_chunk(self):
        """Text shorter than chunk size produces one chunk."""
        chunks = server_mod._chunk_text("short", 800, 100)
        assert len(chunks) == 1
        assert chunks[0] == "short"

    def test_exact_size_produces_overlap_tail(self):
        """Text exactly chunk size produces main chunk + overlap tail."""
        text = "x" * 800
        chunks = server_mod._chunk_text(text, 800, 100)
        # start=0 -> [0:800], start=700 -> [700:800] (overlap tail)
        assert len(chunks) == 2
        assert len(chunks[0]) == 800
        assert len(chunks[1]) == 100

    def test_2000_chars_produces_3_chunks(self):
        """2000 chars with size=800, overlap=100 produces 3 chunks."""
        text = "a" * 2000
        chunks = server_mod._chunk_text(text, 800, 100)
        assert len(chunks) == 3

    def test_overlap_content(self):
        """Adjacent chunks share overlap characters."""
        text = "".join(str(i % 10) for i in range(1600))
        chunks = server_mod._chunk_text(text, 800, 100)
        # 1600 chars: [0:800], [700:1500], [1400:1600] = 3 chunks
        assert len(chunks) == 3
        # Last 100 chars of chunk 0 == first 100 chars of chunk 1
        assert chunks[0][-100:] == chunks[1][:100]

    def test_covers_entire_text(self):
        """All characters in the original text appear in at least one chunk."""
        text = "abcdefghij" * 200  # 2000 chars
        chunks = server_mod._chunk_text(text, 800, 100)
        reconstructed = chunks[0]
        for chunk in chunks[1:]:
            reconstructed += chunk[100:]  # skip overlap portion
        assert reconstructed == text


@pytest.mark.integration
class TestChunkingIntegration:
    """specs/chunking.md -- store_memory chunking integration."""

    def test_short_text_no_chunks(self):
        """Text under 800 chars does not produce chunk rows."""
        server_mod.store_memory("short text", "ag-1", "sess-1")

        # Only the main store call (no chunk stores)
        assert _pg().store.call_count == 1

    def test_long_text_produces_chunks(self):
        """Text over 800 chars produces chunk rows in PG."""
        long_text = "x" * 2000
        server_mod.store_memory(long_text, "ag-1", "sess-1")

        # 1 main store + 3 chunk stores = 4 total
        assert _pg().store.call_count == 4

    def test_chunk_importance_is_zero(self):
        """Chunk rows have importance=0.0."""
        long_text = "y" * 2000
        server_mod.store_memory(long_text, "ag-1", "sess-1")

        # Check chunk store calls (all except first which is the main memory)
        chunk_calls = _pg().store.call_args_list[1:]
        for c in chunk_calls:
            assert c.kwargs.get("importance") == 0.0

    def test_chunk_provenance_has_parent(self):
        """Chunk rows have parent_memory_id and chunk=True in provenance."""
        long_text = "z" * 2000
        result = server_mod.store_memory(long_text, "ag-1", "sess-1")
        parent_id = result["id"]

        chunk_calls = _pg().store.call_args_list[1:]
        for c in chunk_calls:
            prov = c.kwargs.get("provenance", {})
            assert prov["parent_memory_id"] == parent_id
            assert prov["chunk"] is True

    def test_chunk_type_is_episodic(self):
        """Chunk rows have memory_type='episodic'."""
        long_text = "w" * 2000
        server_mod.store_memory(long_text, "ag-1", "sess-1")

        chunk_calls = _pg().store.call_args_list[1:]
        for c in chunk_calls:
            assert c.kwargs.get("memory_type") == "episodic"

    def test_chunk_embed_failure_non_fatal(self):
        """If chunk embedding fails, it's logged but doesn't crash."""
        call_count = [0]
        original_embed = _embedder().embed.return_value

        def embed_side_effect(text):
            call_count[0] += 1
            if call_count[0] > 1:  # first call is for main memory
                raise RuntimeError("embed failed for chunk")
            return [0.1] * 768

        _embedder().embed.side_effect = embed_side_effect

        long_text = "v" * 2000
        result = server_mod.store_memory(long_text, "ag-1", "sess-1")

        # Main store succeeded even though chunk embeds failed
        assert "id" in result
        assert "error" not in result
