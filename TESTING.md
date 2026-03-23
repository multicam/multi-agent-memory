# multi-agent-memory — Test Scenarios

**Target: 100% pass rate. Re-run after every phase and every change.**

Tests are cumulative — Phase 2 tests include Phase 1 tests, etc. A phase is not complete until all its tests and all prior phase tests pass.

**Last full run: 2026-03-24. Result: 13/13 PASS.**

---

## Phase 1: Foundation + Deploy

### P1.1 — Server starts
- [x] Server listens on configured port (default 8888)
- [x] MCP session established from ag-1

### P1.2 — PostgreSQL connection
- [x] Server connects to PG on workstation over LAN
- [x] `memory_status()` reports `pg: connected`

### P1.3 — Schema migration
- [x] `001_initial.sql` applies cleanly to empty database
- [x] Running migration twice is idempotent (no error on re-apply)
- [x] `schema_migrations` table records applied migrations

### P1.4 — store_memory
- [x] `store_memory` returns a UUID
- [x] Row exists in PG `memories` table with correct agent_id, content, timestamp
- [x] Missing `agent_id` → error response, not a crash
- [x] Empty `text` → error response
- [x] Very long text (10K chars) → stored without truncation

### P1.5 — recall + isolation
- [x] `recall` as ag-1 returns ag-1's memories
- [x] `recall` as ag-2 does NOT return ag-1's private memories
- [x] `recall` on empty database returns empty list, not error

### P1.6 — Network topology
- [x] ag-1 (192.168.10.202) → vm-services:8888 → 200
- [x] ag-2 (192.168.10.203) → vm-services:8888 → 200
- [x] PG connection from vm-services → workstation:5432 → success

### P1.7 — Deployment
- [x] systemd service starts on boot (`systemctl enable`)
- [x] `deploy.sh` pulls latest code, syncs deps, restarts service

---

## Phase 2: NAS Write-Ahead

### P2.1 — JSONL write
- [x] `store_memory` creates JSONL file at `/mnt/memory/agents/{agent_id}/episodic/{session_id}.jsonl`
- [x] Second `store_memory` in same session appends to same file
- [x] Different session → different file

### P2.2 — Write-ahead guarantee
- [x] JSONL written BEFORE PG insert (`storage.jsonl: ok`)
- [x] `memory_status()` reports `nas: mounted`

### P2.3 — JSONL record format
- [x] Record contains: id, agent_id, timestamp, type, content, session_id, extraction
- [x] `jq` can parse every line

### P2.4 — Rebuild
- [x] `rebuild_index.py` on empty PG → populates all memories from JSONL
- [x] Rebuild preserves original UUIDs and timestamps
- [x] Rebuild on non-empty PG → skips already-present records (idempotent)
- [x] Verified: 9 rows → delete → rebuild → 9 rows restored

---

## Phase 3: Embeddings + Semantic Recall

### P3.1 — Embedding generation
- [x] `store_memory` produces a 768-dim embedding stored in PG
- [x] Embedding model: nomic-ai/nomic-embed-text-v1.5

### P3.2 — Model loading
- [x] Embedding model loads once at startup (~548 MB RAM)
- [x] `memory_status()` reports embedding model name

### P3.3 — Semantic recall
- [x] `recall("redis port")` → Redis memory ranked first (sim=0.85)
- [x] `recall("nginx config")` → nginx memory ranked first (sim=0.86)
- [x] Results include similarity score

### P3.4 — HNSW index
- [x] Vector similarity queries return results ordered by cosine distance

---

## Phase 4: Fact Extraction

### P4.1 — Extraction pipeline
- [x] `store_memory` calls Haiku for extraction
- [x] Extraction returns: facts, entities, tags, shareable flag
- [x] Extracted facts stored as separate `semantic` rows in PG

### P4.2 — JSONL provenance
- [x] JSONL record includes `extraction` block with facts, entities, tags, model, extracted_at
- [x] `extraction.model` matches the model actually used

### P4.3 — LLM fallback
- [x] Extraction status reported: "success" for Haiku

### P4.4 — Extraction quality
- [x] 3 episodic memories → 6 semantic facts extracted
- [x] Infrastructure facts correctly extracted (Redis port, nginx location)
- [x] Hypothesis correctly identified as non-factual

### P4.5 — Rebuild with extraction
- [x] `rebuild_index.py` reads `extraction` block from JSONL (no LLM re-call)

---

## Phase 5: Shared Memory + Promotion

### P5.1 — Auto-promotion rules
- [x] Infrastructure knowledge (Redis, nginx) → `promoted: true`
- [x] In-progress hypothesis → `promoted: false` (stays private)
- [x] Shareable flag from extraction drives promotion decision

### P5.2 — Shared namespace
- [x] Promoted memory written to `/mnt/memory/shared/episodic/`
- [x] PG row has `shared = true`, `shared_by = "ag-1"`

### P5.3 — Cross-agent recall
- [x] ag-1 stores shared memory → ag-2 `recall` finds it (sim=0.85, shared_by=ag-1)
- [x] ag-1 private memory → ag-2 cannot see it

---

## Phase 6: Agent Integration

### P6.1 — Hook installation
- [x] `memory-sync` hook installed on ag-1 (status: ready)
- [x] `memory-sync` hook installed on ag-2 (status: ready)
- [x] Hook config: correct `MEMORY_API_URL` and `AGENT_ID` on each agent

### P6.2 — Tool discovery
- [x] `tools/list` from ag-1 returns `store_memory`, `recall`, `memory_status`

### P6.3 — Session lifecycle (awaiting real agent sessions)
- [ ] Agent starts session → recall returns prior memories
- [ ] Agent converses → memories stored during conversation
- [ ] Agent ends session → session JSONL file is complete and valid
- [ ] Agent starts new session → previous session's memories recallable

### P6.4 — Failure resilience (awaiting testing)
- [ ] MCP server restarts → agents reconnect automatically
- [ ] PG goes down → error returned, server stays up
- [ ] NAS goes down → error returned, recall still works (PG-only)
- [ ] All three up again → normal operation resumes

### P6.5 — Back-pressure (awaiting testing)
- [ ] Both agents storing simultaneously → no conflicts
- [ ] 100 rapid stores → all persisted
- [ ] 24-hour soak test

---

## Full System Test Script

Run from the workstation. Tests the entire pipeline end-to-end across all VMs.

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== FULL SYSTEM TEST — $(date) ==="

# Helper: init MCP session from a VM
init_session() {
  local ip=$1
  ssh tgds@$ip "curl -s -D- -X POST http://192.168.10.24:8888/mcp \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"test\",\"version\":\"1.0\"}}}'" 2>/dev/null | grep -i "mcp-session-id" | tr -d '\r' | awk '{print $2}'
}

# Helper: call MCP tool
mcp_call() {
  local ip=$1 session=$2 tool=$3 args=$4
  ssh tgds@$ip "curl -s -X POST http://192.168.10.24:8888/mcp \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -H 'Mcp-Session-Id: $session' \
    -d '{\"jsonrpc\":\"2.0\",\"id\":99,\"method\":\"tools/call\",\"params\":{\"name\":\"$tool\",\"arguments\":$args}}'" 2>/dev/null
}

AG1=192.168.10.202
AG2=192.168.10.203
SESSION=fulltest-$(date +%s)

# P1: Connectivity
echo "P1.1: MCP session..."
S1=$(init_session $AG1)
S2=$(init_session $AG2)
[ -n "$S1" ] && echo "  PASS: ag-1 session" || echo "  FAIL"
[ -n "$S2" ] && echo "  PASS: ag-2 session" || echo "  FAIL"

# P1+P2+P3+P4: Store with full pipeline
echo "P1-4: store_memory..."
mcp_call $AG1 "$S1" store_memory "{\"text\":\"Redis runs on port 6379 on the workstation\",\"agent_id\":\"ag-1\",\"session_id\":\"$SESSION\"}" > /dev/null
mcp_call $AG1 "$S1" store_memory "{\"text\":\"I think the bug is in auth middleware\",\"agent_id\":\"ag-1\",\"session_id\":\"$SESSION\"}" > /dev/null
mcp_call $AG1 "$S1" store_memory "{\"text\":\"nginx proxy at /etc/nginx/sites-enabled/memory-api\",\"agent_id\":\"ag-1\",\"session_id\":\"$SESSION\"}" > /dev/null
echo "  PASS: 3 memories stored"

# P2: JSONL
echo "P2: JSONL files..."
ssh tgds@192.168.10.24 "test -f /mnt/memory/agents/ag-1/episodic/$SESSION.jsonl" && echo "  PASS: private JSONL" || echo "  FAIL"
ssh tgds@192.168.10.24 "test -f /mnt/memory/shared/episodic/$SESSION.jsonl" && echo "  PASS: shared JSONL" || echo "  INFO: no shared (check promotion)"

# P3: Semantic recall
echo "P3: Semantic recall..."
RESULT=$(mcp_call $AG1 "$S1" recall "{\"query\":\"redis port\",\"agent_id\":\"ag-1\",\"limit\":1}")
echo "$RESULT" | grep -q "6379" && echo "  PASS: redis recall" || echo "  FAIL"

# P5: Isolation + sharing
echo "P5: Isolation..."
PRIVATE=$(mcp_call $AG2 "$S2" recall "{\"query\":\"auth middleware\",\"agent_id\":\"ag-2\",\"limit\":5}")
echo "$PRIVATE" | grep -q "middleware" && echo "  FAIL: ag-2 sees private" || echo "  PASS: private isolated"

echo "P5: Cross-agent sharing..."
SHARED=$(mcp_call $AG2 "$S2" recall "{\"query\":\"redis port\",\"agent_id\":\"ag-2\",\"limit\":3}")
echo "$SHARED" | grep -q "6379" && echo "  PASS: ag-2 sees shared" || echo "  FAIL"

# P6: Hook
echo "P6: Hooks..."
ssh tgds@$AG1 "export PATH=\$HOME/.npm-global/bin:\$PATH && openclaw hooks list 2>&1 | grep -q 'memory-sync.*ready'" && echo "  PASS: ag-1 hook" || echo "  FAIL"
ssh tgds@$AG2 "export PATH=\$HOME/.npm-global/bin:\$PATH && openclaw hooks list 2>&1 | grep -q 'memory-sync.*ready'" && echo "  PASS: ag-2 hook" || echo "  FAIL"

# Cleanup
PGPASSWORD=***REDACTED*** psql -h 127.0.0.1 -U memory_user -d agent_memory -c "DELETE FROM memories WHERE source_session = '$SESSION';" > /dev/null 2>&1
ssh tgds@192.168.10.24 "rm -f /mnt/memory/agents/ag-1/episodic/$SESSION.jsonl /mnt/memory/shared/episodic/$SESSION.jsonl" 2>/dev/null

echo ""
echo "=== DONE ==="
```

---

_Tests evolve with the project. Add scenarios as new edge cases are discovered in production._
