# Feature: Fact Extraction

## Context
Memory content is processed by an LLM (Haiku primary, Ollama fallback) to extract structured facts, decisions, entities, and tags. This powers semantic memory and promotion decisions.

## Scenarios

### Scenario: clean JSON from LLM parses correctly
- **Given** the LLM returns valid JSON with facts, entities, tags, shareable
- **When** the extractor parses the response
- **Then** all fields are populated in the Extraction dataclass
- **Priority:** critical

### Scenario: JSON wrapped in markdown fences is handled
- **Given** the LLM wraps its response in ```json ... ``` fences
- **When** the extractor parses the response
- **Then** the fences are stripped and JSON parses correctly
- **Priority:** critical

### Scenario: bare fences without language tag handled
- **Given** the LLM wraps its response in ``` ... ``` fences (no language tag)
- **When** the extractor parses the response
- **Then** the fences are stripped and JSON parses correctly
- **Priority:** important

### Scenario: invalid JSON returns empty dict
- **Given** the LLM returns non-JSON text
- **When** the extractor parses the response
- **Then** an empty dict is returned (no crash)
- **Priority:** critical

### Scenario: empty string returns empty dict
- **Given** the LLM returns an empty string
- **When** the extractor parses the response
- **Then** an empty dict is returned (no crash)
- **Priority:** critical

### Scenario: Extraction dataclass serializes all fields
- **Given** an Extraction with facts, decisions, entities, tags, model, status
- **When** to_dict() is called
- **Then** all fields including decisions are present in the output dict
- **Priority:** important

### Scenario: Extraction defaults are sensible
- **Given** an Extraction created with no arguments
- **When** fields are inspected
- **Then** all lists are empty, shareable is False, status is "success"
- **Priority:** important

### Scenario: extraction prompt includes decisions with rationale
- **Given** the EXTRACTION_PROMPT template
- **When** its content is inspected
- **Then** it contains "decisions", "rationale" (case-insensitive), and "WHY"
- **Priority:** important

### Scenario: extraction without API keys skips gracefully
- **Given** a FactExtractor with no API key and no Ollama URL
- **When** extract() is called
- **Then** status is "skipped" and facts list is empty (no crash)
- **Priority:** critical

### Scenario: Haiku failure falls back to Ollama
- **Given** a FactExtractor with both Haiku and Ollama configured
- **And** the Haiku API call raises an exception
- **When** extract() is called
- **Then** the Ollama endpoint is tried
- **Priority:** important

### Scenario: both backends fail returns skipped
- **Given** a FactExtractor with both backends configured
- **And** both API calls raise exceptions
- **When** extract() is called
- **Then** status is "skipped" and facts list is empty
- **Priority:** critical

## Out of Scope
- LLM output quality (non-deterministic; tested via E2E with real memories)
- Ollama model selection (currently hardcoded to llama3)

## Acceptance Criteria
- [ ] All critical scenarios pass
- [ ] No regressions in existing tests
- [ ] Extraction gracefully degrades on any backend failure
