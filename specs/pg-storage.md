# Feature: PostgreSQL Storage

## Context
PGStorage manages all reads/writes to PostgreSQL. It handles connection lifecycle, memory storage with embeddings, fact storage as semantic rows, and three recall methods (semantic, BM25, recency). All operations enforce agent isolation + shared visibility.

## Scenarios

### Scenario: store inserts a memory row
- **Given** a connected PG
- **When** store() is called with memory_id, text, agent_id, session_id
- **Then** a row exists in the memories table with those values
- **Priority:** critical

### Scenario: store with embedding persists vector
- **Given** a connected PG and a 768-dim embedding
- **When** store() is called with the embedding
- **Then** the row's embedding column is populated
- **Priority:** critical

### Scenario: store is idempotent (ON CONFLICT DO NOTHING)
- **Given** a memory_id that already exists in PG
- **When** store() is called with the same memory_id
- **Then** no error is raised and the existing row is unchanged
- **Priority:** critical

### Scenario: store raises when not connected
- **Given** a PGStorage that has not called connect()
- **When** store() is called
- **Then** RuntimeError("Not connected") is raised
- **Priority:** critical

### Scenario: store_facts creates semantic rows
- **Given** a connected PG and 3 extracted facts
- **When** store_facts() is called
- **Then** 3 rows are inserted with memory_type="semantic"
- **And** 3 UUIDs are returned
- **Priority:** critical

### Scenario: store_facts with embeddings persists vectors
- **Given** 3 facts with corresponding 768-dim embeddings
- **When** store_facts() is called
- **Then** each semantic row has its embedding populated
- **Priority:** important

### Scenario: shared store sets shared_by
- **Given** store() called with shared=True
- **When** the row is inspected
- **Then** shared=True and shared_by=agent_id
- **Priority:** critical

### Scenario: is_connected returns correct status
- **Given** a PGStorage
- **When** connect() has not been called
- **Then** is_connected() returns False
- **Priority:** important

### Scenario: count returns row count
- **Given** a connected PG with N memories
- **When** count() is called
- **Then** it returns N
- **Priority:** nice-to-have

## Out of Scope
- Schema migrations (handled by migration scripts)
- Connection pooling (single connection model)
- PG performance tuning

## Acceptance Criteria
- [ ] All critical scenarios pass
- [ ] Every write operation raises RuntimeError when not connected
- [ ] Idempotent inserts never cause duplicate rows
