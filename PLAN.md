# multi-agent-memory — Implementation Plan

**Repo:** `~/Code/multi-agent-memory`
**Design docs:** `~/Code/dark-as-fac/docs/memory-system/`
**Deployment target:** vm-services (192.168.10.24)
**Database:** PostgreSQL + pgvector on workstation (LAN + Tailscale)
**Source of truth:** JSONL on NAS (`/mnt/memory`)

---

## Phase 1: Foundation + Deploy

_Tracer bullet — thinnest possible end-to-end proof._

- [x] PostgreSQL + pgvector on workstation
  - PostgreSQL 16.13, pgvector 0.6.0 installed
  - `agent_memory` database, `memory_user` role created
  - `pg_hba.conf`: vm-services, agent VMs, Tailscale subnet (scram-sha-256)
  - `listen_addresses = '*'`
- [x] Schema migration `001_initial.sql`
  - `memories` table with vector(768), shared namespace columns, provenance JSONB
  - `shared_events` table (append-only inter-agent coordination)
  - `schema_migrations` table (idempotent apply)
  - Indexes: agent+time, HNSW on embedding, GIN on tags, partial on shared
- [x] Python project scaffold
  - `uv init`, deps: fastmcp 3.1.1, psycopg 3.3.3
  - `src/config.py` — env-based config
  - `src/server.py` — FastMCP entry point (streamable-http transport)
  - `src/storage/postgres.py` — PG read/write with dict_row
- [x] MCP tools: `store_memory(text, agent_id, session_id)` → PG insert, returns UUID
- [x] MCP tools: `recall(query, agent_id, limit)` → recency-based recall (semantic search in Phase 3)
- [x] MCP tools: `memory_status()` → health check (PG + NAS status)
- [x] Deploy to vm-services
  - Ansible role `memory-server` in tgds-office-config
  - `services-install.yml` playbook: nas + agent-memory + git-credentials + memory-server
  - Cloned via HTTPS to `/opt/multi-agent-memory`
  - systemd service enabled and running (94 MB RAM)
  - `deploy/deploy.sh` for upgrades
- [x] Verified: ag-1 → vm-services:8888 → PG on workstation → store + recall + status all passing

**Completed: 2026-03-23. All Phase 1 tests passing.**

---

## Phase 2: NAS Write-Ahead (Diderot Layer)

- [ ] Verify CIFS mount on vm-services (`/mnt/memory`)
- [ ] `src/storage/jsonl.py` — append to JSONL, read JSONL
- [ ] `store_memory` writes JSONL first (durable), then PG (best-effort)
- [ ] File path: `/mnt/memory/agents/{agent_id}/episodic/{session_id}.jsonl`
- [ ] JSONL record format: id, agent_id, timestamp, type, content, session_id, metadata
- [ ] `scripts/rebuild_index.py` — read all JSONL, insert into PG (skip extraction)
- [ ] Verify: drop PG data, run rebuild, all memories restored

**Done when:** every `store_memory` call produces a JSONL record on NAS, and `rebuild_index.py` restores PG from scratch.

---

## Phase 3: Embeddings + Semantic Recall

- [ ] `src/embeddings.py` — SentenceTransformers wrapper (nomic-embed-text-v1.5, 768-dim)
- [ ] Model loaded once at server startup, kept in memory (~548 MB)
- [ ] `store_memory` generates embedding, stores in PG `memories.embedding`
- [ ] `recall` uses cosine similarity (`<=>` operator) via HNSW index
- [ ] Similarity threshold parameter (default 0.7)
- [ ] JSONL record does NOT store embeddings (re-generated on rebuild)
- [ ] Update `rebuild_index.py` to generate embeddings during replay
- [ ] Verify: `recall("nginx configuration")` returns relevant memory, not unrelated text

**Done when:** recall returns semantically ranked results from pgvector.

---

## Phase 4: Fact Extraction

- [ ] `src/extraction/facts.py` — LLM-based extraction
- [ ] Extraction prompt: input text → structured JSON (facts, entities, tags, shareable flag)
- [ ] Primary model: claude-haiku-4-5 via anthropic SDK
- [ ] Fallback: Ollama (local) if API unavailable
- [ ] On `store_memory`: extract facts, store as separate `semantic` rows in PG linked to source
- [ ] JSONL record includes `extraction` block: facts, entities, tags, model, extracted_at, shareable
- [ ] `provenance` JSONB column populated with extraction metadata
- [ ] Update `rebuild_index.py` to use stored extractions (no re-calling LLM)
- [ ] Verify: store a conversation turn, query for a specific entity → get the extracted fact

**Done when:** every stored memory has structured facts extracted and indexed, and rebuilds use cached extractions.

---

## Phase 5: Shared Memory + Promotion

- [ ] `src/extraction/promotion.py` — rule-based auto-promotion
- [ ] Auto-share rules: infrastructure knowledge, domain facts, tool commands, error resolutions
- [ ] Keep private: in-progress hypotheses, session-specific context, failed attempts
- [ ] On `store_memory`: if `shareable == true`, write to both private and `shared/episodic/` on NAS
- [ ] PG: set `shared = true`, `shared_by = agent_id` on promoted memories
- [ ] `recall` searches private + shared by default
- [ ] `scripts/curate.py` — batch LLM review of recent private memories for missed promotions
- [ ] Verify: ag-1 stores infra fact → auto-promotes → ag-2 can recall it

**Done when:** agents have isolated private memories with automatic knowledge sharing through the shared namespace.

---

## Phase 6: Agent Integration

- [ ] Determine MCP transport for OpenClaw (stdio vs HTTP/SSE)
- [ ] Configure ag-1 and ag-2 to connect to memory MCP server on vm-services:8888
- [ ] Test full agent session lifecycle: conversation → store → end session → new session → recall
- [ ] Verify auto-capture pattern: every meaningful turn retained without explicit agent action
- [ ] Session-start injection: `recall("recent context for {agent_id}")` → prepend to agent context
- [ ] Test shared memory: ag-1 learns something, ag-2 benefits in a separate session
- [ ] Monitor: memory count, extraction latency, recall quality

**Done when:** both agents use memory transparently in real sessions.

---

## Deferred Phases

_These are future capabilities, not committed work. They are subject to redesign once we have usage data from the active phases above. Priorities and approach will evolve based on what we observe in production. Promote on demand._

### D1: BM25 Keyword Search (2nd retrieval channel)

- [ ] Add PostgreSQL full-text search (tsvector/tsquery) index on `memories.content`
- [ ] Second retrieval path in `recall`: keyword match alongside semantic
- [ ] Reciprocal Rank Fusion to merge semantic + keyword results

**Trigger:** when semantic search alone misses exact-match queries (proper nouns, error codes, specific commands).

### D2: Entity Resolution

- [ ] `entities` table: canonical entity references (id, name, type, aliases, summary)
- [ ] `entity_mentions` table: maps extracted entity strings to canonical entity IDs
- [ ] spaCy NER + string similarity for alias detection ("Alice" = "my coworker Alice")
- [ ] Entity-scoped recall: "what do we know about Alice?"

**Trigger:** when agents accumulate enough entities to have duplicates or ambiguous references.

### D3: Graph Traversal (3rd retrieval channel)

- [ ] `entity_relationships` table: directed edges between entities (entity_a, relation, entity_b)
- [ ] Spreading activation: start from top semantic matches, follow graph edges with decay
- [ ] Third retrieval path in `recall`, merged via RRF with semantic + BM25

**Trigger:** when fact recall requires multi-hop reasoning ("who works at the company that has the API issue?").

### D4: Cross-Encoder Reranking

- [ ] Post-retrieval reranking using a cross-encoder model (e.g., cross-encoder/ms-marco-MiniLM-L-6-v2)
- [ ] Applied after RRF fusion, before returning top-k results
- [ ] ~100 MB model, runs on CPU

**Trigger:** when top-k precision matters and the current ranking returns near-misses in top positions.

### D5: Reflect Operation

- [ ] Periodic analysis of stored memories to generate higher-order insights
- [ ] "What patterns emerge from ag-1's last 50 sessions?"
- [ ] Output stored as `procedural` memory type
- [ ] LLM-powered, batch job (not real-time)

**Trigger:** after weeks of agent operation, when enough episodic data exists to extract meaningful patterns.

### D6: Nightly Curation Cron

- [ ] Schedule `curate.py` as a systemd timer or cron job
- [ ] Configurable schedule (nightly to start, adjust based on volume)
- [ ] Reports: what was promoted, what was skipped, confidence scores

**Trigger:** after Phase 5 promotion rules are validated manually and auto-promotion is trusted.

### D7: Web Admin UI

- [ ] Read-only interface for browsing memories, entities, shared pool
- [ ] Memory timeline, agent comparison, extraction quality review
- [ ] Lightweight Flask/FastAPI app, same server or separate

**Trigger:** when JM needs to inspect memory state beyond `psql` queries and JSONL grep.

### D8: Remote Access (Tailscale)

- [ ] Bind MCP server to Tailscale interface in addition to LAN
- [ ] Agents on remote Macs can connect to memory server
- [ ] Auth: Tailscale ACLs + optional API key on MCP server

**Trigger:** when agents run outside the local Proxmox network.

---

## Deployment Checklist (reference for Phase 1 and ongoing)

### Workstation (PostgreSQL)
```bash
sudo apt install postgresql-16 postgresql-16-pgvector
sudo -u postgres createuser memory_user --pwprompt
sudo -u postgres createdb agent_memory --owner=memory_user
sudo -u postgres psql -d agent_memory -c "CREATE EXTENSION vector;"
# Edit postgresql.conf: listen_addresses = '*'
# Edit pg_hba.conf: add vm-services, agent VMs, Tailscale subnet
sudo systemctl reload postgresql
```

### vm-services (MCP server)
```bash
sudo git clone git@github.com:multicam/multi-agent-memory.git /opt/multi-agent-memory
cd /opt/multi-agent-memory
uv sync
# Create .env with PG_URL, NAS_PATH, ANTHROPIC_API_KEY
sudo cp deploy/agent-memory.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agent-memory
```

### Upgrade procedure
```bash
ssh tgds@192.168.10.24 "cd /opt/multi-agent-memory && ./deploy/deploy.sh"
# deploy.sh: git pull && uv sync && sudo systemctl restart agent-memory
```
