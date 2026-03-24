"""Tests for Reciprocal Rank Fusion merge algorithm."""

from src.storage.postgres import rrf_merge


def _doc(id: str, **kwargs) -> dict:
    return {"id": id, "content": f"doc-{id}", **kwargs}


def test_single_channel_semantic():
    """RRF with only semantic results passes through with scores."""
    semantic = [_doc("a", similarity=0.9), _doc("b", similarity=0.7)]
    result = rrf_merge(semantic, [], limit=10)
    assert len(result) == 2
    assert result[0]["id"] == "a"
    assert result[1]["id"] == "b"
    assert all("rrf_score" in r for r in result)


def test_single_channel_bm25():
    """RRF with only BM25 results passes through with scores."""
    bm25 = [_doc("x", bm25_rank=0.5), _doc("y", bm25_rank=0.3)]
    result = rrf_merge([], bm25, limit=10)
    assert len(result) == 2
    assert result[0]["id"] == "x"
    assert all("rrf_score" in r for r in result)


def test_both_channels_doc_in_both_ranks_highest():
    """A document appearing in both channels should rank higher than one in just one."""
    semantic = [_doc("shared"), _doc("sem-only")]
    bm25 = [_doc("shared"), _doc("bm25-only")]
    result = rrf_merge(semantic, bm25, limit=10)

    ids = [r["id"] for r in result]
    assert ids[0] == "shared"  # dual-channel doc ranks first
    assert len(result) == 3


def test_rrf_scores_are_additive():
    """RRF score for dual-channel doc = sum of both channel contributions."""
    k = 60
    semantic = [_doc("a")]
    bm25 = [_doc("a")]
    result = rrf_merge(semantic, bm25, k=k, limit=10)

    expected = 2.0 / (k + 1)  # rank 1 in both channels
    assert abs(result[0]["rrf_score"] - expected) < 1e-6


def test_limit_respected():
    """RRF should return at most `limit` results."""
    semantic = [_doc(str(i)) for i in range(20)]
    result = rrf_merge(semantic, [], limit=5)
    assert len(result) == 5


def test_empty_both_channels():
    """RRF with no results returns empty list."""
    assert rrf_merge([], [], limit=10) == []


def test_preserves_semantic_fields():
    """RRF keeps similarity field from semantic version of docs."""
    semantic = [_doc("a", similarity=0.85, memory_type="semantic")]
    bm25 = [_doc("a", bm25_rank=0.5)]
    result = rrf_merge(semantic, bm25, limit=10)
    assert result[0]["similarity"] == 0.85  # semantic version kept


def test_ordering_is_stable():
    """Documents with equal RRF scores maintain deterministic ordering."""
    semantic = [_doc("a"), _doc("b"), _doc("c")]
    bm25 = []
    result = rrf_merge(semantic, bm25, limit=10)
    assert result[0]["id"] == "a"
    assert result[1]["id"] == "b"
    assert result[2]["id"] == "c"
