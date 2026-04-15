"""Tests for fact extraction JSON parsing."""

import json
import pytest
from unittest.mock import MagicMock, patch

from src.extraction.facts import FactExtractor, Extraction


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_anthropic_response(text: str) -> MagicMock:
    """Return a mock shaped like an Anthropic Messages API response."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


def _make_ollama_response(payload: dict) -> MagicMock:
    """Return a mock httpx Response carrying a JSON Ollama body."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": json.dumps(payload)}
    return mock_resp


def test_parse_json_clean():
    """Clean JSON parses correctly."""
    ext = FactExtractor()
    data, ok = ext._parse_json('{"facts": ["a"], "entities": [], "tags": [], "shareable": true}')
    assert data["facts"] == ["a"]
    assert data["shareable"] is True
    assert ok is True


def test_parse_json_with_markdown_fences():
    """JSON wrapped in markdown fences is handled."""
    ext = FactExtractor()
    data, ok = ext._parse_json('```json\n{"facts": ["b"], "entities": [], "tags": [], "shareable": false}\n```')
    assert data["facts"] == ["b"]
    assert ok is True


def test_parse_json_with_fences_no_lang():
    """JSON wrapped in bare fences (no language tag) is handled."""
    ext = FactExtractor()
    data, ok = ext._parse_json('```\n{"facts": [], "entities": [], "tags": ["test"], "shareable": false}\n```')
    assert data["tags"] == ["test"]
    assert ok is True


def test_parse_json_invalid():
    """Invalid JSON returns empty dict and parse_ok=False."""
    ext = FactExtractor()
    data, ok = ext._parse_json("not json at all")
    assert data == {}
    assert ok is False


def test_parse_json_empty():
    """Empty string returns empty dict and parse_ok=False."""
    ext = FactExtractor()
    data, ok = ext._parse_json("")
    assert data == {}
    assert ok is False


def test_extraction_dataclass_to_dict():
    """Extraction.to_dict() returns all fields including decisions."""
    e = Extraction(
        facts=["fact1"],
        decisions=["Decided X because Y"],
        entities=[{"name": "X", "type": "tool"}],
        tags=["infra"],
        shareable=True,
        model="haiku",
        extracted_at="2026-01-01",
        status="success",
    )
    d = e.to_dict()
    assert d["facts"] == ["fact1"]
    assert d["decisions"] == ["Decided X because Y"]
    assert d["model"] == "haiku"
    assert d["shareable"] is True


def test_extraction_defaults():
    """Extraction defaults are sensible."""
    e = Extraction()
    assert e.facts == []
    assert e.decisions == []
    assert e.entities == []
    assert e.tags == []
    assert e.shareable is False
    assert e.status == "success"


def test_extraction_prompt_contains_decisions_field():
    """Extraction prompt asks for decisions with rationale."""
    from src.extraction.facts import EXTRACTION_PROMPT
    assert "decisions" in EXTRACTION_PROMPT
    assert "rationale" in EXTRACTION_PROMPT.lower()
    assert "WHY" in EXTRACTION_PROMPT


def test_extract_without_api_keys_returns_skipped():
    """Extract without any API key skips gracefully."""
    ext = FactExtractor(api_key=None, ollama_base_url=None)
    result = ext.extract("some text")
    assert result.status == "skipped"
    assert result.facts == []


# ---------------------------------------------------------------------------
# FactExtractor initialisation
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFactExtractorInit:
    def test_api_key_creates_anthropic_client(self):
        """Providing an api_key instantiates an anthropic.Anthropic client."""
        with patch("src.extraction.facts.anthropic.Anthropic") as mock_cls:
            ext = FactExtractor(api_key="sk-test-key")
            mock_cls.assert_called_once_with(api_key="sk-test-key")
            assert ext._client is mock_cls.return_value

    def test_no_api_key_leaves_client_none(self):
        """Without api_key the internal client stays None."""
        ext = FactExtractor(api_key=None)
        assert ext._client is None

    def test_ollama_base_url_stored(self):
        """ollama_base_url is stored for later use."""
        ext = FactExtractor(ollama_base_url="http://localhost:11434")
        assert ext._ollama_base_url == "http://localhost:11434"


# ---------------------------------------------------------------------------
# extract() routing
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestExtractRouting:
    _GOOD_JSON = json.dumps({
        "facts": ["Redis runs on 6379"],
        "decisions": ["Decided to use Redis because it is fast"],
        "entities": [{"name": "Redis", "type": "service"}],
        "tags": ["cache"],
        "shareable": True,
    })

    def test_extract_uses_haiku_when_client_present(self):
        """extract() delegates to _extract_haiku when the Anthropic client exists."""
        with patch("src.extraction.facts.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = (
                _make_anthropic_response(self._GOOD_JSON)
            )
            ext = FactExtractor(api_key="sk-test")
            result = ext.extract("Redis caches things")

        assert result.status == "success"
        assert result.model == "claude-haiku-4-5-20251001"
        assert result.facts == ["Redis runs on 6379"]

    def test_extract_haiku_failure_falls_back_to_ollama(self):
        """When Haiku raises, extract() tries Ollama next."""
        fallback_payload = {
            "facts": ["fallback fact"],
            "decisions": [],
            "entities": [],
            "tags": ["fallback"],
            "shareable": False,
        }
        with patch("src.extraction.facts.anthropic.Anthropic") as mock_cls, \
             patch("httpx.post", return_value=_make_ollama_response(fallback_payload)):
            mock_cls.return_value.messages.create.side_effect = RuntimeError("timeout")

            ext = FactExtractor(
                api_key="sk-test",
                ollama_base_url="http://localhost:11434",
            )
            result = ext.extract("some text")

        assert result.status == "fallback"
        assert result.model == "ollama/llama3"
        assert result.facts == ["fallback fact"]

    def test_extract_haiku_and_ollama_failure_returns_skipped(self):
        """When both backends fail extract() returns status='skipped'."""
        with patch("src.extraction.facts.anthropic.Anthropic") as mock_cls, \
             patch("httpx.post") as mock_post:
            mock_cls.return_value.messages.create.side_effect = RuntimeError("haiku down")
            mock_post.side_effect = RuntimeError("ollama down")

            ext = FactExtractor(
                api_key="sk-test",
                ollama_base_url="http://localhost:11434",
            )
            result = ext.extract("some text")

        assert result.status == "skipped"
        assert result.facts == []


# ---------------------------------------------------------------------------
# _extract_haiku()
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestExtractHaiku:
    _NOW = "2026-04-09T00:00:00+00:00"
    _PAYLOAD = {
        "facts": ["Python 3.12 is fast"],
        "decisions": ["Decided to use Python 3.12 because of speed improvements"],
        "entities": [{"name": "Python", "type": "tool"}],
        "tags": ["python"],
        "shareable": True,
    }

    def test_happy_path_returns_extraction(self):
        """_extract_haiku() parses the LLM response into an Extraction."""
        with patch("src.extraction.facts.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = (
                _make_anthropic_response(json.dumps(self._PAYLOAD))
            )
            ext = FactExtractor(api_key="sk-test")
            result = ext._extract_haiku("Python 3.12 is fast", self._NOW)

        assert result.status == "success"
        assert result.model == "claude-haiku-4-5-20251001"
        assert result.extracted_at == self._NOW
        assert result.facts == ["Python 3.12 is fast"]
        assert result.shareable is True

    def test_calls_anthropic_messages_create(self):
        """_extract_haiku() calls client.messages.create with the right model."""
        with patch("src.extraction.facts.anthropic.Anthropic") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.return_value = (
                _make_anthropic_response(json.dumps(self._PAYLOAD))
            )
            ext = FactExtractor(api_key="sk-test")
            ext._extract_haiku("text", self._NOW)

            mock_client.messages.create.assert_called_once()
            call_kwargs = mock_client.messages.create.call_args
            assert call_kwargs.kwargs["model"] == "claude-haiku-4-5-20251001"

    def test_empty_response_json_returns_empty_lists(self):
        """_extract_haiku() handles an empty JSON object gracefully."""
        with patch("src.extraction.facts.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = (
                _make_anthropic_response("{}")
            )
            ext = FactExtractor(api_key="sk-test")
            result = ext._extract_haiku("text", self._NOW)

        assert result.facts == []
        assert result.decisions == []
        assert result.shareable is False

    def test_unparseable_response_sets_parse_error_status(self):
        """2026-04-15 P1: JSON parse failure surfaces as status='parse_error', not 'success'."""
        with patch("src.extraction.facts.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = (
                _make_anthropic_response("not json at all")
            )
            ext = FactExtractor(api_key="sk-test")
            result = ext._extract_haiku("text", self._NOW)

        assert result.status == "parse_error"
        assert result.facts == []

    def test_embed_batch_used_for_facts_or_chunks(self):
        """Regression stub: embed_batch is a supported batch API."""
        # Isolated from server; just ensures Embedder.embed_batch exists + usable.
        from src.embeddings import Embedder
        from unittest.mock import MagicMock

        e = Embedder()
        mock_model = MagicMock()
        mock_vec1, mock_vec2 = MagicMock(), MagicMock()
        mock_vec1.tolist.return_value = [0.1] * 768
        mock_vec2.tolist.return_value = [0.2] * 768
        mock_model.encode.return_value = [mock_vec1, mock_vec2]
        e._model = mock_model

        out = e.embed_batch(["a", "b"])
        assert len(out) == 2
        mock_model.encode.assert_called_once_with(["a", "b"], normalize_embeddings=True)


# ---------------------------------------------------------------------------
# _extract_ollama()
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestExtractOllama:
    _NOW = "2026-04-09T00:00:00+00:00"
    _PAYLOAD = {
        "facts": ["Ollama runs locally"],
        "decisions": [],
        "entities": [],
        "tags": ["llm"],
        "shareable": False,
    }

    def test_happy_path_returns_fallback_extraction(self):
        """_extract_ollama() returns an Extraction with status='fallback'."""
        with patch("httpx.post", return_value=_make_ollama_response(self._PAYLOAD)):
            ext = FactExtractor(ollama_base_url="http://localhost:11434")
            result = ext._extract_ollama("text", self._NOW)

        assert result.status == "fallback"
        assert result.model == "ollama/llama3"
        assert result.extracted_at == self._NOW
        assert result.facts == ["Ollama runs locally"]

    def test_calls_correct_endpoint(self):
        """_extract_ollama() POSTs to <base_url>/api/generate."""
        with patch("httpx.post", return_value=_make_ollama_response(self._PAYLOAD)) as mock_post:
            ext = FactExtractor(ollama_base_url="http://localhost:11434")
            ext._extract_ollama("text", self._NOW)

            call_args = mock_post.call_args
            assert call_args.args[0] == "http://localhost:11434/api/generate"
            assert call_args.kwargs["json"]["model"] == "llama3"
            assert call_args.kwargs["json"]["stream"] is False

    def test_raises_on_http_error(self):
        """_extract_ollama() propagates HTTP errors so extract() can catch them."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = RuntimeError("HTTP 500")

        with patch("httpx.post", return_value=mock_resp):
            ext = FactExtractor(ollama_base_url="http://localhost:11434")
            with pytest.raises(RuntimeError, match="HTTP 500"):
                ext._extract_ollama("text", self._NOW)
