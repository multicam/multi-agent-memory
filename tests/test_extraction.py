"""Tests for fact extraction JSON parsing."""

from src.extraction.facts import FactExtractor, Extraction


def test_parse_json_clean():
    """Clean JSON parses correctly."""
    ext = FactExtractor()
    result = ext._parse_json('{"facts": ["a"], "entities": [], "tags": [], "shareable": true}')
    assert result["facts"] == ["a"]
    assert result["shareable"] is True


def test_parse_json_with_markdown_fences():
    """JSON wrapped in markdown fences is handled."""
    ext = FactExtractor()
    result = ext._parse_json('```json\n{"facts": ["b"], "entities": [], "tags": [], "shareable": false}\n```')
    assert result["facts"] == ["b"]


def test_parse_json_with_fences_no_lang():
    """JSON wrapped in bare fences (no language tag) is handled."""
    ext = FactExtractor()
    result = ext._parse_json('```\n{"facts": [], "entities": [], "tags": ["test"], "shareable": false}\n```')
    assert result["tags"] == ["test"]


def test_parse_json_invalid():
    """Invalid JSON returns empty dict."""
    ext = FactExtractor()
    result = ext._parse_json("not json at all")
    assert result == {}


def test_parse_json_empty():
    """Empty string returns empty dict."""
    ext = FactExtractor()
    result = ext._parse_json("")
    assert result == {}


def test_extraction_dataclass_to_dict():
    """Extraction.to_dict() returns all fields."""
    e = Extraction(
        facts=["fact1"],
        entities=[{"name": "X", "type": "tool"}],
        tags=["infra"],
        shareable=True,
        model="haiku",
        extracted_at="2026-01-01",
        status="success",
    )
    d = e.to_dict()
    assert d["facts"] == ["fact1"]
    assert d["model"] == "haiku"
    assert d["shareable"] is True


def test_extraction_defaults():
    """Extraction defaults are sensible."""
    e = Extraction()
    assert e.facts == []
    assert e.entities == []
    assert e.tags == []
    assert e.shareable is False
    assert e.status == "success"


def test_extract_without_api_keys_returns_skipped():
    """Extract without any API key skips gracefully."""
    ext = FactExtractor(api_key=None, ollama_base_url=None)
    result = ext.extract("some text")
    assert result.status == "skipped"
    assert result.facts == []
