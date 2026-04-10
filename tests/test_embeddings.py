"""Tests for the Embedder class (src/embeddings.py)."""

import pytest
from unittest.mock import MagicMock, patch

from src.embeddings import Embedder, MODEL_NAME, DIMENSIONS


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestEmbedderLoad:
    def test_load_creates_sentence_transformer(self):
        """load() instantiates a SentenceTransformer with trust_remote_code=True."""
        with patch("src.embeddings.SentenceTransformer") as mock_cls:
            embedder = Embedder()
            embedder.load()

            mock_cls.assert_called_once_with(MODEL_NAME, trust_remote_code=True)
            assert embedder._model is mock_cls.return_value

    def test_load_uses_custom_model_name(self):
        """load() passes the model_name given at construction to SentenceTransformer."""
        with patch("src.embeddings.SentenceTransformer") as mock_cls:
            embedder = Embedder(model_name="custom/model")
            embedder.load()

            mock_cls.assert_called_once_with("custom/model", trust_remote_code=True)


# ---------------------------------------------------------------------------
# embed()
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestEmbedderEmbed:
    def _loaded_embedder(self, encode_return=None) -> Embedder:
        """Return an Embedder whose model is already loaded (no real network)."""
        mock_model = MagicMock()
        vec = MagicMock()
        vec.tolist.return_value = encode_return if encode_return is not None else [0.1] * DIMENSIONS
        mock_model.encode.return_value = vec

        with patch("src.embeddings.SentenceTransformer", return_value=mock_model):
            embedder = Embedder()
            embedder.load()
        return embedder

    def test_embed_before_load_raises_runtime_error(self):
        """embed() raises RuntimeError when called before load()."""
        embedder = Embedder()
        with pytest.raises(RuntimeError, match="not loaded"):
            embedder.embed("hello")

    def test_embed_calls_encode_with_normalize(self):
        """embed() calls model.encode with normalize_embeddings=True."""
        embedder = self._loaded_embedder()
        embedder.embed("test text")

        embedder._model.encode.assert_called_once_with(
            "test text", normalize_embeddings=True
        )

    def test_embed_returns_list(self):
        """embed() returns a plain Python list, not a numpy array."""
        expected = [0.5] * DIMENSIONS
        embedder = self._loaded_embedder(encode_return=expected)
        result = embedder.embed("test text")

        assert isinstance(result, list)
        assert result == expected


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestEmbedderProperties:
    def test_dimensions_returns_768(self):
        """dimensions property returns the module-level DIMENSIONS constant."""
        assert Embedder().dimensions == 768
        assert Embedder().dimensions == DIMENSIONS

    def test_model_name_returns_default(self):
        """model_name property returns the default model name when none specified."""
        assert Embedder().model_name == MODEL_NAME

    def test_model_name_returns_custom(self):
        """model_name property reflects the name passed at construction."""
        assert Embedder(model_name="my/model").model_name == "my/model"
