"""Embedding generation using SentenceTransformers."""

import logging
import threading

from sentence_transformers import SentenceTransformer

log = logging.getLogger("agent-memory")

MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
DIMENSIONS = 768


class Embedder:
    def __init__(self, model_name: str = MODEL_NAME):
        self._model_name = model_name
        self._model: SentenceTransformer | None = None
        self._lock = threading.Lock()

    def load(self) -> None:
        log.info("Loading embedding model: %s", self._model_name)
        self._model = SentenceTransformer(self._model_name, trust_remote_code=True)
        log.info("Embedding model loaded (%d-dim)", DIMENSIONS)

    def embed(self, text: str) -> list[float]:
        """Generate a 768-dim embedding for the given text."""
        if not self._model:
            raise RuntimeError("Embedding model not loaded")
        with self._lock:
            vec = self._model.encode(text, normalize_embeddings=True)
        return vec.tolist()

    @property
    def dimensions(self) -> int:
        return DIMENSIONS

    @property
    def model_name(self) -> str:
        return self._model_name
