# Verbatim Chunking

## Context

Long memories (>800 chars) are split into overlapping chunks for
better semantic recall. Chunks are embedding-only search index entries --
no LLM extraction, importance=0.0, parent linkage via provenance.

## Scenarios

### Chunk Boundaries

GIVEN text of 2000 characters with CHUNK_SIZE=800, CHUNK_OVERLAP=100
WHEN _chunk_text is called
THEN produces 3 chunks with correct start/end positions

### Overlap

GIVEN adjacent chunks
WHEN examining their content
THEN the last 100 chars of chunk N overlap with the first 100 chars of chunk N+1

### Short Text Skipped

GIVEN text shorter than CHUNK_SIZE (800 chars)
WHEN store_memory is called
THEN no chunk rows are created

### Parent Provenance

GIVEN a chunked memory
WHEN chunk rows are stored in PG
THEN each chunk's provenance contains {"parent_memory_id": <id>, "chunk": true}

### Chunk Properties

GIVEN a chunk row in PG
WHEN examining its fields
THEN importance=0.0, memory_type="episodic", has embedding, no LLM extraction
