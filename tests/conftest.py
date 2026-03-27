"""Shared fixtures and automatic marker assignment for the test suite."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.extraction.facts import Extraction


# ---------------------------------------------------------------------------
# Automatic marker: tests without an explicit marker get @pytest.mark.unit
# ---------------------------------------------------------------------------

def pytest_collection_modifyitems(items):
    """Tag unmarked tests as 'unit' automatically."""
    for item in items:
        markers = {m.name for m in item.iter_markers()}
        if not markers & {"unit", "integration", "e2e"}:
            item.add_marker(pytest.mark.unit)


# ---------------------------------------------------------------------------
# Extraction fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_extraction():
    """A typical extraction result with shareable infrastructure content."""
    return Extraction(
        facts=["Redis runs on port 6379", "PostgreSQL on port 5432"],
        decisions=["Decided to use pgvector because it avoids a separate vector DB"],
        entities=[
            {"name": "Redis", "type": "service"},
            {"name": "PostgreSQL", "type": "service"},
        ],
        tags=["infrastructure", "database"],
        shareable=True,
        model="claude-haiku-4-5-20251001",
        extracted_at="2026-03-24T00:00:00+00:00",
        status="success",
    )


@pytest.fixture
def private_extraction():
    """An extraction that should NOT be promoted."""
    return Extraction(
        facts=["The bug might be in auth middleware"],
        decisions=[],
        entities=[],
        tags=["debugging", "hypothesis"],
        shareable=False,
        model="claude-haiku-4-5-20251001",
        extracted_at="2026-03-24T00:00:00+00:00",
        status="success",
    )


@pytest.fixture
def skipped_extraction():
    """An extraction where no backend was available."""
    return Extraction(status="skipped")


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
