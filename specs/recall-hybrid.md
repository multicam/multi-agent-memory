# Feature: Hybrid Recall (Semantic + BM25 + RRF)

## Context
Recall uses two retrieval channels (semantic embedding similarity and BM25 keyword match) merged via Reciprocal Rank Fusion. Falls back to recency if both channels fail. Visibility: agent's own memories + shared memories from other agents.

## Scenarios

### Scenario: BM25 returns keyword-matched results
- **Given** a connected PG with memories containing "nginx config on port 80"
- **When** recall_bm25("nginx port", "ag-1") is called
- **Then** the result includes the nginx memory with a bm25_rank score
- **Priority:** critical

### Scenario: BM25 returns empty on no match
- **Given** a connected PG with no matching memories
- **When** recall_bm25("nonexistent query", "ag-1") is called
- **Then** an empty list is returned
- **Priority:** critical

### Scenario: BM25 uses plainto_tsquery for safe input
- **Given** a query with special characters ("user's query with special chars")
- **When** recall_bm25() is called
- **Then** the SQL uses plainto_tsquery (not raw to_tsquery)
- **Priority:** critical

### Scenario: BM25 includes shared memories in results
- **Given** a memory stored by ag-1 with shared=True
- **When** recall_bm25() is called by ag-2
- **Then** the SQL includes shared=TRUE visibility
- **Priority:** critical

### Scenario: BM25 raises when not connected
- **Given** a PGStorage that has not called connect()
- **When** recall_bm25() is called
- **Then** RuntimeError("Not connected") is raised
- **Priority:** critical

### Scenario: semantic recall returns similarity-scored results
- **Given** a connected PG with embedded memories
- **When** recall_semantic() is called with a query embedding
- **Then** results include a similarity score and respect visibility rules
- **Priority:** critical

### Scenario: recency fallback returns time-ordered results
- **Given** both semantic and BM25 channels return empty
- **When** recall() is called
- **Then** results are ordered by created_at DESC
- **Priority:** important

### Scenario: RRF with single semantic channel
- **Given** only semantic results (no BM25)
- **When** rrf_merge() is called
- **Then** results pass through with rrf_score added
- **Priority:** important

### Scenario: RRF with single BM25 channel
- **Given** only BM25 results (no semantic)
- **When** rrf_merge() is called
- **Then** results pass through with rrf_score added
- **Priority:** important

### Scenario: document in both channels ranks highest
- **Given** a document appearing in both semantic and BM25 results
- **When** rrf_merge() is called
- **Then** the dual-channel document ranks first
- **Priority:** critical

### Scenario: RRF scores are additive across channels
- **Given** a document at rank 1 in both channels with k=60
- **When** rrf_merge() is called
- **Then** its rrf_score equals 2.0 / (60 + 1)
- **Priority:** important

### Scenario: RRF respects limit
- **Given** 20 results from semantic channel
- **When** rrf_merge(limit=5) is called
- **Then** at most 5 results are returned
- **Priority:** important

### Scenario: RRF with empty inputs
- **Given** no results from either channel
- **When** rrf_merge() is called
- **Then** an empty list is returned
- **Priority:** critical

### Scenario: RRF preserves semantic fields
- **Given** a document with similarity=0.85 from semantic and bm25_rank=0.5 from BM25
- **When** rrf_merge() is called
- **Then** the result preserves the similarity field from the semantic version
- **Priority:** important

### Scenario: RRF ordering is stable for equal scores
- **Given** multiple documents with equal RRF scores
- **When** rrf_merge() is called
- **Then** ordering is deterministic (input order preserved)
- **Priority:** nice-to-have

## Out of Scope
- Cross-encoder reranking (deferred phase D4)
- Graph traversal (deferred phase D3)
- Embedding model accuracy (model-specific, not testable in unit tests)

## Acceptance Criteria
- [ ] All critical scenarios pass
- [ ] BM25 never uses raw to_tsquery (SQL injection risk)
- [ ] Visibility rules enforce agent isolation + shared access
- [ ] RRF produces correct scores for dual-channel documents
