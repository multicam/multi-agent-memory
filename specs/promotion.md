# Feature: Memory Promotion

## Context
Memories are auto-promoted to the shared namespace based on the LLM's shareable flag and tag-based rules. Infrastructure knowledge is shared; in-progress work stays private. This enables cross-agent learning.

## Scenarios

### Scenario: LLM says shareable with no conflicting tags
- **Given** an extraction with shareable=True and tags=["infrastructure"]
- **When** should_promote() is called
- **Then** it returns True
- **Priority:** critical

### Scenario: LLM says shareable but private tags block
- **Given** an extraction with shareable=True and tags=["in-progress", "debugging"]
- **When** should_promote() is called
- **Then** it returns False (private tags override LLM)
- **Priority:** critical

### Scenario: LLM says private but shareable tags override
- **Given** an extraction with shareable=False and tags=["infrastructure", "deployment"]
- **When** should_promote() is called
- **Then** it returns True (shareable tags override LLM)
- **Priority:** critical

### Scenario: LLM says private with no shareable tags
- **Given** an extraction with shareable=False and tags=["personal", "random"]
- **When** should_promote() is called
- **Then** it returns False
- **Priority:** critical

### Scenario: empty tags + not shareable stays private
- **Given** an extraction with shareable=False and tags=[]
- **When** should_promote() is called
- **Then** it returns False
- **Priority:** important

### Scenario: empty tags + shareable is promoted
- **Given** an extraction with shareable=True and tags=[]
- **When** should_promote() is called
- **Then** it returns True (no private tags to block)
- **Priority:** important

### Scenario: all shareable tag categories trigger promotion
- **Given** an extraction with shareable=False
- **When** should_promote() is called with each tag from the SHAREABLE_TAGS set
- **Then** each returns True
- **Priority:** important

### Scenario: all private tag categories block promotion
- **Given** an extraction with shareable=True
- **When** should_promote() is called with each tag from the PRIVATE_TAGS set
- **Then** each returns False
- **Priority:** important

### Scenario: tags are compared case-insensitively
- **Given** an extraction with tags in mixed case (e.g. "Infrastructure", "IN-PROGRESS")
- **When** should_promote() is called
- **Then** tag matching works regardless of case
- **Priority:** important

## Out of Scope
- Nightly curation re-review (handled by curate.py, separate workflow)
- Custom promotion rules per agent

## Acceptance Criteria
- [ ] All critical scenarios pass
- [ ] No regressions in existing tests
- [ ] Private tags always block, even when LLM says shareable
