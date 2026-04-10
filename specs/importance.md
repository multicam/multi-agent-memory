# Importance Scoring

## Context

Heuristic scoring (0.0-1.0) using extraction richness + keyword signals.
No LLM needed. Used to populate the `importance` column in PG.

## Scenarios

### Extraction Richness

GIVEN an extraction with 3+ facts, decisions, shareable flag, and entities
WHEN score_importance is called
THEN score reflects cumulative richness (baseline 0.3 + increments)

### Keyword Signals

GIVEN text containing importance keywords ("decided", "always", "never", etc.)
WHEN score_importance is called
THEN score increases by 0.05 per keyword hit (capped at 0.15)

### Scale Bounds

GIVEN any combination of extraction + keywords
WHEN score_importance is called
THEN score is always between 0.0 and 1.0

### Baseline Score

GIVEN an empty extraction with no keyword hits
WHEN score_importance is called
THEN score equals 0.3 (baseline)

### Maximum Score

GIVEN a rich extraction with many keyword hits
WHEN score_importance is called
THEN score caps at 1.0
