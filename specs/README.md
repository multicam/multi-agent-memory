# Test Scenarios

This directory contains feature specifications in Given/When/Then format.
Each file defines scenarios for one feature. Claude Code is the step-definition engine.

## Format

See the tdd-qa skill's `references/scenario-format.md` for the full spec.

## Quick Example

### Scenario: [name]
- **Given** [precondition]
- **When** [action]
- **Then** [observable outcome]
- **Priority:** critical | important | nice-to-have

## Test Pyramid Mapping

| Layer | Marker | What it tests | Speed |
|-------|--------|---------------|-------|
| Unit | `@pytest.mark.unit` | Pure logic, no I/O | <1s total |
| Integration | `@pytest.mark.integration` | Mocked PG/NAS/LLM boundaries | <5s total |
| E2E | `@pytest.mark.e2e` | Live infra (vm-services, PG, NAS) | Minutes |

## Running

```bash
# All unit + integration (default)
uv run pytest

# Unit only
uv run pytest -m unit

# Integration only
uv run pytest -m integration

# With coverage
uv run pytest --cov

# Quality gate (backtest)
uv run python scripts/backtest.py
```
