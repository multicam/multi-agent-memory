# Feature: Store Memory

## Context
The store_memory MCP tool is the primary write path. It orchestrates: validation, embedding generation, fact extraction, promotion decision, JSONL write-ahead (NAS), and PG insert. Write-ahead guarantees JSONL is durable before PG is attempted.

## Scenarios

### Scenario: empty text is rejected
- **Given** a store_memory call with text=""
- **When** the tool executes
- **Then** an error dict is returned with "text cannot be empty"
- **Priority:** critical

### Scenario: empty agent_id is rejected
- **Given** a store_memory call with agent_id=""
- **When** the tool executes
- **Then** an error dict is returned with "agent_id is required"
- **Priority:** critical

### Scenario: successful store returns expected fields
- **Given** valid text, agent_id, session_id
- **And** both JSONL and PG backends are available
- **When** store_memory is called
- **Then** the response contains id (UUID), agent_id, session_id, created_at, promoted, extraction summary, storage status
- **Priority:** critical

### Scenario: JSONL written before PG (write-ahead)
- **Given** valid input
- **When** store_memory is called
- **Then** JSONL append happens before PG insert
- **And** if PG fails, the JSONL record still exists
- **Priority:** critical

### Scenario: promoted memory written to shared JSONL
- **Given** a memory whose extraction triggers promotion (e.g. tag "infrastructure")
- **When** store_memory is called
- **Then** the record is appended to both agent-private and shared JSONL paths
- **Priority:** critical

### Scenario: non-promoted memory stays private
- **Given** a memory whose extraction does not trigger promotion
- **When** store_memory is called
- **Then** the record is only in the agent-private JSONL, not shared
- **Priority:** critical

### Scenario: both JSONL and PG fail returns error
- **Given** NAS is unmounted and PG is disconnected
- **When** store_memory is called
- **Then** an error dict is returned ("Both JSONL and PG writes failed")
- **Priority:** critical

### Scenario: embedding failure is non-fatal
- **Given** the embedder raises an exception
- **When** store_memory is called
- **Then** the memory is still stored (without embedding)
- **And** PG row has embedding=NULL
- **Priority:** important

### Scenario: extraction facts stored as semantic rows
- **Given** extraction returns 3 facts
- **When** store_memory is called
- **Then** pg.store_facts() is called with those 3 facts
- **And** they are stored as memory_type="semantic"
- **Priority:** important

### Scenario: very long text stored without truncation
- **Given** text of 10,000+ characters
- **When** store_memory is called
- **Then** the full text is stored in both JSONL and PG
- **Priority:** important

## Out of Scope
- JSONL file format details (covered in jsonl-storage spec)
- Embedding model behavior (covered in embeddings spec)
- Promotion rule logic (covered in promotion spec)

## Acceptance Criteria
- [ ] All critical scenarios pass
- [ ] Write-ahead guarantee: JSONL always before PG
- [ ] Both backends failing is handled gracefully
- [ ] Extraction failures don't block storage
