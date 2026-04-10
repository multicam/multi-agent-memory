# Duplicate Detection

## Context

Near-duplicate detection via embedding cosine similarity before storage.
Threshold 0.92. Graceful degradation when PG is unavailable.

## Scenarios

### Exact Duplicate

GIVEN a memory with identical content already exists for this agent
WHEN store_memory is called with the same text
THEN return {"status": "duplicate", "existing_id": <id>} without storing

### Near Duplicate (Above Threshold)

GIVEN a memory with >0.92 cosine similarity exists for this agent
WHEN store_memory is called with similar text
THEN return duplicate status with existing memory ID

### Below Threshold

GIVEN the closest existing memory has <0.92 cosine similarity
WHEN store_memory is called
THEN proceed with normal storage (no duplicate detection)

### PG Unavailable

GIVEN PostgreSQL is down or check_duplicate raises an exception
WHEN store_memory is called
THEN skip dedup check and proceed with normal storage (write-ahead guarantee preserved)

### No Embedding

GIVEN embedding generation failed (embedding is None)
WHEN store_memory is called
THEN skip dedup check entirely
