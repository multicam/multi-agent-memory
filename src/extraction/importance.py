"""Heuristic importance scoring for memories.

Scores 0.0-1.0 using extraction richness + keyword signals.
No LLM needed — pure rule-based scoring from extraction output.
"""

from src.extraction.facts import Extraction

IMPORTANCE_KEYWORDS = frozenset({
    "decided", "always", "never", "convention", "standard",
    "important", "critical", "must", "rule", "preference",
})


def score_importance(text: str, extraction: Extraction) -> float:
    """Score a memory's importance from 0.0 to 1.0.

    Baseline 0.3 avoids inflation above the schema default (0.5).
    Extraction richness and keyword signals add incremental weight.
    """
    score = 0.3

    # Extraction richness
    if len(extraction.facts) >= 3:
        score += 0.15
    if extraction.decisions:
        score += 0.2
    if extraction.shareable:
        score += 0.1
    if extraction.entities:
        score += 0.05

    # Keyword signals
    words = set(text.lower().split())
    keyword_hits = len(words & IMPORTANCE_KEYWORDS)
    score += min(keyword_hits * 0.05, 0.15)

    return min(score, 1.0)
