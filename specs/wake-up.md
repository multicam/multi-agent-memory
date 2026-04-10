# Wake-Up Protocol

## Context

Layered memory recall for session start. Returns importance-ranked
critical memories + recent decisions. Replaces generic `recall` call
in the session-start hook.

## Scenarios

### Layer Structure

GIVEN an agent with stored memories of varying importance
WHEN wake_up is called
THEN return dict with layer_1_critical, layer_2_decisions, and token_estimate

### Importance Ordering

GIVEN memories with different importance scores
WHEN wake_up returns layer_1_critical
THEN memories are ordered by importance DESC, created_at DESC

### Decision Recall

GIVEN memories containing "decided", "because", or "chose"
WHEN wake_up returns layer_2_decisions
THEN those decision memories appear in layer_2_decisions

### Token Estimate

GIVEN wake_up results
WHEN token_estimate is calculated
THEN it equals sum of len(content) // 4 for all returned memories

### Visibility Rules

GIVEN shared memories from other agents
WHEN wake_up is called
THEN shared memories are included alongside agent's own
