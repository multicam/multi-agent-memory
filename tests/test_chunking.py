"""Tests for verbatim chunking (specs/chunking.md).

Tests the _chunk_text helper and the chunking integration in store_memory.
"""

import pytest
from tests.helpers import make_extraction

import src.server as server_mod


@pytest.fixture(autouse=True)
def _patch_extractor(server_mocks):
    """Override the default extraction to use tags=["test"] for chunking tests."""
    server_mocks.extractor.extract.return_value = make_extraction(tags=["test"])
    # Stash mocks for direct access in test methods
    _patch_extractor.mocks = server_mocks


def _pg():
    return _patch_extractor.mocks.pg


def _embedder():
    return _patch_extractor.mocks.embedder


@pytest.mark.unit
class TestChunkText:
    """specs/chunking.md -- chunk boundary math."""

    def test_short_text_single_chunk(self):
        """Text shorter than chunk size produces one chunk."""
        chunks = server_mod._chunk_text("short", 800, 100)
        assert len(chunks) == 1
        assert chunks[0] == "short"

    def test_exact_size_no_short_tail(self):
        """Text exactly chunk size produces one chunk (100-char tail is dropped)."""
        text = "x" * 800
        chunks = server_mod._chunk_text(text, 800, 100)
        # Tail [700:800] is only 100 chars == _CHUNK_MIN, so dropped
        assert len(chunks) == 1
        assert len(chunks[0]) == 800

    def test_2000_chars_produces_3_chunks(self):
        """2000 chars with size=800, overlap=100 produces 3 chunks."""
        text = "a" * 2000
        chunks = server_mod._chunk_text(text, 800, 100)
        assert len(chunks) == 3

    def test_overlap_content(self):
        """Adjacent chunks share overlap characters."""
        text = "".join(str(i % 10) for i in range(1600))
        chunks = server_mod._chunk_text(text, 800, 100)
        # 1600 chars: [0:800], [700:1500] = 2 chunks (tail [1400:1600] = 200 chars > _CHUNK_MIN)
        assert len(chunks) >= 2
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

    def test_overlap_ge_size_raises(self):
        """overlap >= size raises ValueError to prevent infinite loops."""
        with pytest.raises(ValueError, match="overlap.*must be < size"):
            server_mod._chunk_text("text", 100, 100)
        with pytest.raises(ValueError, match="overlap.*must be < size"):
            server_mod._chunk_text("text", 100, 200)

    def test_text_length_equals_overlap_produces_single_chunk(self):
        """Text exactly the overlap length produces one chunk, not zero.

        Guards the `and chunks` branch in the drop-trailing-tail check:
        for the first chunk, `chunks` is empty, so len(chunk) <= overlap
        must still keep it. 2026-04-15 review P2.
        """
        chunks = server_mod._chunk_text("01234", 10, 5)
        assert len(chunks) == 1
        assert chunks[0] == "01234"

    def test_short_trailing_chunk_dropped(self):
        """Trailing chunks shorter than _CHUNK_MIN are dropped."""
        # 850 chars with size=800, overlap=100: main [0:800], tail [700:850] = 150 chars
        # 150 > _CHUNK_MIN (100), so tail IS kept
        text = "a" * 850
        chunks = server_mod._chunk_text(text, 800, 100)
        assert len(chunks) == 2

        # 810 chars: main [0:800], tail [700:810] = 110 chars > 100, kept
        text = "b" * 810
        chunks = server_mod._chunk_text(text, 800, 100)
        assert len(chunks) == 2

        # 800 chars: main [0:800], tail [700:800] = 100 chars == _CHUNK_MIN, dropped
        text = "c" * 800
        chunks = server_mod._chunk_text(text, 800, 100)
        assert len(chunks) == 1


@pytest.mark.integration
class TestChunkingIntegration:
    """specs/chunking.md -- store_memory chunking integration.

    After the 2026-04-15 P1 fix, store_memory calls the aggregate
    PGStorage.store_with_facts_and_chunks() in a single transaction. Tests
    now inspect that call's kwargs.
    """

    def test_short_text_no_chunks(self):
        """Text under 800 chars passes an empty chunks list to the aggregate."""
        server_mod.store_memory("short text", "ag-1", "sess-1")

        _pg().store_with_facts_and_chunks.assert_called_once()
        kw = _pg().store_with_facts_and_chunks.call_args.kwargs
        assert kw["chunks"] == []

    def test_long_text_produces_chunks(self):
        """Text over 800 chars passes a 3-element chunks list to the aggregate."""
        long_text = "x" * 2000
        server_mod.store_memory(long_text, "ag-1", "sess-1")

        kw = _pg().store_with_facts_and_chunks.call_args.kwargs
        assert len(kw["chunks"]) == 3

    def test_chunk_embeddings_passed_through(self):
        """chunk_embeddings arg carries one vector per chunk."""
        long_text = "y" * 2000
        server_mod.store_memory(long_text, "ag-1", "sess-1")

        kw = _pg().store_with_facts_and_chunks.call_args.kwargs
        assert len(kw["chunk_embeddings"]) == len(kw["chunks"])

    def test_chunk_list_matches_chunk_text_helper(self):
        """server.store_memory passes the output of _chunk_text to PG."""
        long_text = "z" * 2000
        server_mod.store_memory(long_text, "ag-1", "sess-1")

        kw = _pg().store_with_facts_and_chunks.call_args.kwargs
        expected = server_mod._chunk_text(long_text, 800, 100)
        assert kw["chunks"] == expected

    def test_chunk_embed_failure_non_fatal(self):
        """If chunk embedding fails, chunk_embeddings becomes [None...] and store still runs."""
        def batch_side_effect(texts):
            # Fail on the chunk batch (size > 1 in this test -- 3 chunks)
            if len(texts) > 1:
                raise RuntimeError("embed failed for chunks")
            return [[0.1] * 768 for _ in texts]

        _embedder().embed_batch.side_effect = batch_side_effect

        long_text = "v" * 2000
        result = server_mod.store_memory(long_text, "ag-1", "sess-1")

        # Main store still succeeded; chunk_embeddings is a list of Nones
        assert "id" in result
        assert "error" not in result
        kw = _pg().store_with_facts_and_chunks.call_args.kwargs
        assert all(e is None for e in kw["chunk_embeddings"])
