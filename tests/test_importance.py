"""Tests for importance scoring (specs/importance.md)."""

import pytest
from src.extraction.facts import Extraction
from src.extraction.importance import score_importance


def _extraction(**kwargs) -> Extraction:
    """Build an Extraction with sensible defaults, overridden by kwargs."""
    defaults = dict(
        facts=[], decisions=[], entities=[], tags=[],
        shareable=False, model="test", extracted_at="", status="success",
    )
    defaults.update(kwargs)
    return Extraction(**defaults)


@pytest.mark.unit
class TestExtractionRichness:
    """specs/importance.md -- extraction richness scoring."""

    def test_baseline_empty_extraction(self):
        """Empty extraction with no keywords scores 0.3 (baseline)."""
        ext = _extraction()
        assert score_importance("some plain text", ext) == pytest.approx(0.3)

    def test_three_facts_adds_015(self):
        """3+ facts contribute 0.15."""
        ext = _extraction(facts=["a", "b", "c"])
        assert score_importance("plain text", ext) == pytest.approx(0.45)

    def test_decisions_add_020(self):
        """Having decisions contributes 0.2."""
        ext = _extraction(decisions=["Decided X because Y"])
        assert score_importance("plain text", ext) == pytest.approx(0.5)

    def test_shareable_adds_010(self):
        """Shareable flag contributes 0.1."""
        ext = _extraction(shareable=True)
        assert score_importance("plain text", ext) == pytest.approx(0.4)

    def test_entities_add_005(self):
        """Having entities contributes 0.05."""
        ext = _extraction(entities=[{"name": "Redis", "type": "service"}])
        assert score_importance("plain text", ext) == pytest.approx(0.35)

    def test_cumulative_richness(self):
        """All richness signals accumulate."""
        ext = _extraction(
            facts=["a", "b", "c"],
            decisions=["Decided X"],
            shareable=True,
            entities=[{"name": "Redis", "type": "service"}],
        )
        # 0.3 + 0.15 + 0.2 + 0.1 + 0.05 = 0.8
        assert score_importance("plain text", ext) == pytest.approx(0.8)


@pytest.mark.unit
class TestKeywordSignals:
    """specs/importance.md -- keyword signal scoring."""

    def test_single_keyword(self):
        """One importance keyword adds 0.05."""
        ext = _extraction()
        assert score_importance("we decided to use Redis", ext) == pytest.approx(0.35)

    def test_multiple_keywords(self):
        """Multiple keywords each add 0.05."""
        ext = _extraction()
        assert score_importance("we always decided this is critical", ext) == pytest.approx(0.45)

    def test_keyword_cap_at_015(self):
        """Keyword contribution caps at 0.15 (3 hits)."""
        ext = _extraction()
        text = "decided always never convention standard important critical must rule preference"
        assert score_importance(text, ext) == pytest.approx(0.45)

    def test_case_insensitive(self):
        """Keywords are matched case-insensitively."""
        ext = _extraction()
        assert score_importance("We DECIDED to ALWAYS use it", ext) == pytest.approx(0.4)


@pytest.mark.unit
class TestScaleBounds:
    """specs/importance.md -- scale bounds."""

    def test_never_below_baseline(self):
        """Score is never below 0.3 (baseline)."""
        ext = _extraction()
        assert score_importance("", ext) >= 0.3

    def test_never_above_one(self):
        """Score caps at 1.0 even with all signals maxed."""
        ext = _extraction(
            facts=["a", "b", "c"],
            decisions=["Decided X"],
            shareable=True,
            entities=[{"name": "Redis", "type": "service"}],
        )
        text = "decided always never convention standard important critical must rule preference"
        result = score_importance(text, ext)
        assert result <= 1.0
        # 0.3 + 0.15 + 0.2 + 0.1 + 0.05 + 0.15 = 0.95
        assert result == pytest.approx(0.95)
