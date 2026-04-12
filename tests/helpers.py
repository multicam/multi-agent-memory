"""Shared test helpers -- importable from any test module.

Unlike conftest.py (loaded by pytest, not importable), this module provides
factory functions and utilities that tests can `from tests.helpers import ...`.
"""

from src.extraction.facts import Extraction


def make_extraction(**kwargs) -> Extraction:
    """Build an Extraction with sensible defaults, overridden by kwargs."""
    defaults = dict(
        facts=[], decisions=[], entities=[], tags=[],
        shareable=False, model="test", extracted_at="2026-04-09T00:00:00+00:00",
        status="success",
    )
    defaults.update(kwargs)
    return Extraction(**defaults)
