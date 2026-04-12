"""Shared fixtures and automatic marker assignment for the test suite."""

import os
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

os.environ.setdefault("PG_URL", "postgresql://mock:mock@localhost/test")

from tests.helpers import make_extraction


# ---------------------------------------------------------------------------
# Automatic marker: tests without an explicit marker get @pytest.mark.unit
# ---------------------------------------------------------------------------

def pytest_collection_modifyitems(items):
    """Tag unmarked tests as 'unit' automatically."""
    for item in items:
        markers = {m.name for m in item.iter_markers()}
        if not markers & {"unit", "integration", "e2e"}:
            item.add_marker(pytest.mark.unit)


@pytest.fixture
def sample_extraction():
    """A typical extraction result with shareable infrastructure content."""
    return make_extraction(
        facts=["Redis runs on port 6379", "PostgreSQL on port 5432"],
        decisions=["Decided to use pgvector because it avoids a separate vector DB"],
        entities=[
            {"name": "Redis", "type": "service"},
            {"name": "PostgreSQL", "type": "service"},
        ],
        tags=["infrastructure", "database"],
        shareable=True,
        model="claude-haiku-4-5-20251001",
    )


@pytest.fixture
def private_extraction():
    """An extraction that should NOT be promoted."""
    return make_extraction(
        facts=["The bug might be in auth middleware"],
        tags=["debugging", "hypothesis"],
        model="claude-haiku-4-5-20251001",
    )


@pytest.fixture
def skipped_extraction():
    """An extraction where no backend was available."""
    return make_extraction(status="skipped")


# ---------------------------------------------------------------------------
# Mock PG storage
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_pg():
    """PGStorage with a mocked connection (no real DB needed)."""
    from src.storage.postgres import PGStorage
    storage = PGStorage("postgresql://mock:mock@localhost/test")
    storage._conn = MagicMock()
    return storage


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def now():
    """A fixed UTC datetime for deterministic tests."""
    return datetime(2026, 3, 24, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Server module globals mock (shared across test files)
# ---------------------------------------------------------------------------

class ServerMocks:
    """Container for server module mocks, accessible via fixture or class attrs."""

    def __init__(self, pg, jsonl, embedder, extractor, config=None):
        self.pg = pg
        self.jsonl = jsonl
        self.embedder = embedder
        self.extractor = extractor
        self.config = config


@pytest.fixture
def server_mocks(monkeypatch):
    """Patch server module globals with mocks. Returns a ServerMocks container.

    Usage in tests: access mocks via server_mocks.pg, server_mocks.embedder, etc.
    """
    import src.server as server_mod

    mock_pg = MagicMock()
    mock_pg.is_connected.return_value = True
    mock_pg.check_duplicate.return_value = None

    mock_jsonl = MagicMock()
    mock_jsonl.is_mounted.return_value = True

    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [0.1] * 768
    mock_embedder.model_name = "test-model"
    mock_embedder.dimensions = 768

    mock_extractor = MagicMock()
    mock_extractor.extract.return_value = make_extraction(
        facts=["fact1"],
        decisions=[],
        entities=[],
        tags=["test"],
    )

    monkeypatch.setattr(server_mod, "pg", mock_pg)
    monkeypatch.setattr(server_mod, "jsonl", mock_jsonl)
    monkeypatch.setattr(server_mod, "embedder", mock_embedder)
    monkeypatch.setattr(server_mod, "extractor", mock_extractor)

    return ServerMocks(mock_pg, mock_jsonl, mock_embedder, mock_extractor)
