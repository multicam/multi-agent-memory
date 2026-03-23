# multi-agent-memory — Test Scenarios

**Target: 100% pass rate. Re-run after every phase and every change.**

Tests are cumulative — Phase 2 tests include Phase 1 tests, etc. A phase is not complete until all its tests and all prior phase tests pass.

---

## Phase 1: Foundation + Deploy

### P1.1 — Server starts
- [ ] `uv run src/server.py` starts without error
- [ ] Server listens on configured port (default 8888)
- [ ] `memory_status()` returns `{"pg": "connected", "nas": "unknown"}` (NAS not wired yet)

### P1.2 — PostgreSQL connection
- [ ] Server connects to PG on workstation over LAN
- [ ] Connection failure → server starts but `memory_status()` reports `{"pg": "disconnected"}`
- [ ] Server does not crash if PG is unreachable at startup (graceful degradation)

### P1.3 — Schema migration
- [ ] `001_initial.sql` applies cleanly to empty database
- [ ] Running migration twice is idempotent (no error on re-apply)
- [ ] `schema_migrations` table records applied migrations

### P1.4 — store_memory
- [ ] `store_memory(text="hello", agent_id="ag-1", session_id="sess-1")` returns a UUID
- [ ] Row exists in PG `memories` table with correct agent_id, content, timestamp
- [ ] Missing `agent_id` → error response, not a crash
- [ ] Empty `text` → error response
- [ ] Very long text (10K chars) → stored without truncation

### P1.5 — recall
- [ ] `recall(query="hello", agent_id="ag-1")` returns the stored memory
- [ ] `recall(query="hello", agent_id="ag-2")` returns empty (isolation)
- [ ] `recall` with `limit=1` returns at most 1 result
- [ ] `recall` on empty database returns empty list, not error

### P1.6 — Network topology
- [ ] curl from ag-1 (192.168.10.202) → vm-services:8888 → response
- [ ] curl from ag-2 (192.168.10.203) → vm-services:8888 → response
- [ ] curl from workstation → vm-services:8888 → response
- [ ] PG connection from vm-services → workstation:5432 → success
- [ ] PG connection from random IP not in pg_hba.conf → rejected

### P1.7 — Deployment
- [ ] systemd service starts on boot (`systemctl enable`)
- [ ] `systemctl restart agent-memory` restarts cleanly
- [ ] `deploy.sh` pulls latest code, syncs deps, restarts service
- [ ] Server logs go to journald (`journalctl -u agent-memory`)

### P1.8 — Back-pressure
- [ ] 100 sequential `store_memory` calls → all succeed, no timeouts
- [ ] 10 concurrent `store_memory` calls → all succeed (no PG connection pool exhaustion)
- [ ] `recall` during active `store_memory` writes → returns results, no blocking
- [ ] PG goes down mid-operation → error returned, server stays up, reconnects when PG returns

---

## Phase 2: NAS Write-Ahead

### P2.1 — JSONL write
- [ ] `store_memory` creates JSONL file at `/mnt/memory/agents/{agent_id}/episodic/{session_id}.jsonl`
- [ ] JSONL record contains: id, agent_id, timestamp, type, content, session_id, metadata
- [ ] Second `store_memory` in same session appends to same file (not overwrites)
- [ ] Different session → different file

### P2.2 — Write-ahead guarantee
- [ ] JSONL is written BEFORE PG insert
- [ ] If PG insert fails, JSONL file still contains the record
- [ ] `memory_status()` now reports NAS mount status: `{"pg": "connected", "nas": "mounted"}`

### P2.3 — NAS failure handling
- [ ] NAS unmounted → `store_memory` returns error (not silent failure)
- [ ] NAS unmounted → server stays up, reports `{"nas": "unmounted"}`
- [ ] NAS remounted → next `store_memory` works without restart

### P2.4 — Rebuild
- [ ] `rebuild_index.py` on empty PG → populates all memories from JSONL
- [ ] Rebuild preserves original UUIDs and timestamps (not generating new ones)
- [ ] Rebuild on non-empty PG → skips already-present records (idempotent)
- [ ] Rebuild reports: records processed, records inserted, records skipped

### P2.5 — File integrity
- [ ] JSONL files are valid JSON lines (each line parseable independently)
- [ ] `jq` can process every line: `jq -c '.' < file.jsonl` succeeds
- [ ] No partial writes: if server crashes mid-write, file is not corrupted (line-buffered flush)

### P2.6 — Back-pressure
- [ ] 100 sequential stores → 100 JSONL records, 100 PG rows
- [ ] NAS latency spike (CIFS slow) → store completes within 10s timeout, no hang
- [ ] Concurrent writes to same session file → no interleaved lines (append is atomic per record)
- [ ] Rebuild from 10K records → completes, PG contains 10K rows

---

## Phase 3: Embeddings + Semantic Recall

### P3.1 — Embedding generation
- [ ] `store_memory` produces a 768-dim embedding stored in PG
- [ ] Embedding is non-zero (not a null vector)
- [ ] Same text → same embedding (deterministic)
- [ ] Empty text → handled (error or zero vector, not crash)

### P3.2 — Model loading
- [ ] Embedding model loads once at startup (~548 MB RAM)
- [ ] Server memory usage stable after loading (no leak per request)
- [ ] Model name logged at startup for provenance

### P3.3 — Semantic recall
- [ ] `recall("nginx config")` returns memory about nginx, not unrelated memories
- [ ] `recall("nginx config")` ranks nginx memory higher than a memory about "python setup"
- [ ] `recall` returns similarity score with each result
- [ ] `recall` with similarity threshold filters low-relevance results

### P3.4 — HNSW index
- [ ] `EXPLAIN` on recall query shows "Index Scan using idx_memories_embedding"
- [ ] After 1000 inserts, index is used (not seq scan)
- [ ] Index rebuild (`REINDEX`) completes without error

### P3.5 — Rebuild with embeddings
- [ ] `rebuild_index.py` generates embeddings during replay (JSONL has no embeddings)
- [ ] After rebuild, `recall` with semantic search works identically to pre-rebuild state

### P3.6 — Back-pressure
- [ ] Embedding generation adds < 100ms per `store_memory` call
- [ ] 10 concurrent `recall` queries → all return within 500ms
- [ ] 50 concurrent `recall` queries → all return, no PG connection pool exhaustion
- [ ] Server under load: memory usage stays under 2 GB (model + PG pool + buffers)

---

## Phase 4: Fact Extraction

### P4.1 — Extraction pipeline
- [ ] `store_memory` calls Haiku for extraction
- [ ] Extraction returns: facts (list), entities (list), tags (list), shareable (bool)
- [ ] Extracted facts stored as separate `semantic` rows in PG
- [ ] Source episodic memory linked to derived semantic rows

### P4.2 — JSONL provenance
- [ ] JSONL record includes `extraction` block with: facts, entities, tags, model, extracted_at, shareable
- [ ] `extraction.model` matches the model actually used (haiku vs ollama)

### P4.3 — LLM fallback
- [ ] Anthropic API down → falls back to Ollama
- [ ] Ollama down too → memory stored without extraction (raw only), logged as warning
- [ ] Extraction failure does NOT prevent the memory from being stored
- [ ] `provenance.extraction_status` records: "success", "fallback", or "skipped"

### P4.4 — Extraction quality
- [ ] Input: "Alice from Acme called about API rate limits" → extracts entity "Alice", entity "Acme", fact about rate limits
- [ ] Input: meaningless text ("asdf jkl") → extracts empty facts, not hallucinated facts
- [ ] Input: very long text (5K chars) → extraction completes within 10s

### P4.5 — Rebuild with extraction
- [ ] `rebuild_index.py` reads `extraction` block from JSONL, inserts facts directly (no LLM call)
- [ ] Rebuild produces identical semantic rows as original extraction

### P4.6 — Back-pressure
- [ ] Extraction adds < 3s per `store_memory` call (Haiku latency)
- [ ] 10 concurrent stores with extraction → all complete, no API rate limit errors
- [ ] LLM timeout (30s) → extraction skipped, memory still stored with warning
- [ ] Extraction cost tracking: log token count per extraction call

---

## Phase 5: Shared Memory + Promotion

### P5.1 — Auto-promotion rules
- [ ] Memory tagged as infrastructure knowledge → `shared = true` automatically
- [ ] Memory tagged as in-progress work → stays private (`shared = false`)
- [ ] Shareable flag from extraction drives the promotion decision

### P5.2 — Shared namespace
- [ ] Promoted memory written to `/mnt/memory/shared/episodic/{session_id}.jsonl`
- [ ] Promoted memory in PG has `shared = true`, `shared_by = "ag-1"` (source agent)
- [ ] Original private copy remains in agent's own JSONL and PG rows

### P5.3 — Cross-agent recall
- [ ] ag-1 stores promoted memory → ag-2 `recall` finds it
- [ ] ag-2 stores private memory → ag-1 `recall` does NOT find it
- [ ] `recall` searches both private + shared by default
- [ ] `recall(shared_only=true)` returns only shared memories

### P5.4 — Curation batch
- [ ] `curate.py` reads recent private memories, calls LLM to assess shareability
- [ ] Memories promoted by curation get `shared = true`, `shared_by` set, copied to shared JSONL
- [ ] `curate.py` is idempotent (running twice doesn't duplicate shared records)
- [ ] `curate.py` reports: reviewed N, promoted M, skipped K

### P5.5 — Back-pressure
- [ ] 1000 memories, 100 shared → `recall` performance unchanged (index handles mixed queries)
- [ ] Promotion writes to 2 JSONL paths (private + shared) → both complete atomically
- [ ] NAS slow → promotion degrades gracefully (private write succeeds, shared write retried)
- [ ] `curate.py` on 1000 private memories → completes within 5 minutes

---

## Phase 6: Agent Integration

### P6.1 — MCP transport
- [ ] OpenClaw on ag-1 connects to MCP server on vm-services:8888
- [ ] OpenClaw on ag-2 connects to same MCP server
- [ ] Both agents can call `store_memory`, `recall`, `memory_status` tools

### P6.2 — Session lifecycle
- [ ] Agent starts session → `recall("recent context for ag-1")` returns prior memories
- [ ] Agent converses → memories stored during conversation
- [ ] Agent ends session → session JSONL file is complete and valid
- [ ] Agent starts new session → previous session's memories recallable

### P6.3 — Cross-agent sharing (end-to-end)
- [ ] ag-1 learns "use port 3001 for the dev server" → auto-promoted
- [ ] ag-2 asks about dev server port → recalls ag-1's knowledge
- [ ] Provenance visible: `shared_by: "ag-1"` in the result

### P6.4 — Failure resilience
- [ ] MCP server restarts → agents reconnect automatically (or error clearly)
- [ ] PG goes down → agents get error on `store_memory`, `recall` returns cached/empty
- [ ] NAS goes down → agents get error on `store_memory`, `recall` still works (PG-only)
- [ ] All three up again → normal operation resumes without manual intervention

### P6.5 — Back-pressure
- [ ] Both agents storing simultaneously → no conflicts, no lost writes
- [ ] Agent stores 100 memories in rapid succession → all persisted to JSONL + PG
- [ ] Agent recalls during heavy writes from other agent → latency < 1s
- [ ] 24-hour soak test: agents running real sessions, memory count grows, recall stays fast

---

## Regression Suite

_Run these after every change, every phase completion, every deploy._

### R1 — Smoke test (30 seconds)
```bash
# From workstation or ag-1
curl -s http://192.168.10.24:8888/health  # or memory_status MCP call
# Expect: {"pg": "connected", "nas": "mounted"}
```

### R2 — Round-trip test (1 minute)
```bash
# Store → recall → verify
STORE_RESULT=$(curl -s -X POST http://192.168.10.24:8888/store \
  -d '{"text": "regression test $(date)", "agent_id": "test", "session_id": "regression"}')
RECALL_RESULT=$(curl -s http://192.168.10.24:8888/recall \
  -d '{"query": "regression test", "agent_id": "test"}')
# Expect: RECALL_RESULT contains the stored text
```

### R3 — Rebuild integrity (5 minutes)
```bash
# Dump PG row count → drop data → rebuild → verify count matches
psql -c "SELECT count(*) FROM memories;" agent_memory
# ... truncate, rebuild, re-count
```

### R4 — Isolation test (1 minute)
```bash
# Store as ag-1, recall as ag-2 → expect empty
# Store as ag-1 with shareable → recall as ag-2 → expect found
```

---

_Tests evolve with the project. Add scenarios as new edge cases are discovered in production._
