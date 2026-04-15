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
  - `shared_events` table (append-only inter-agent coordination) — dropped in `003_drop_shared_events.sql` (unused)
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

- [x] Verified CIFS mount on vm-services (`/mnt/memory`) with uid=tgds
- [x] `src/storage/jsonl.py` — append per-session JSONL, read-all sorted by timestamp
- [x] `store_memory` writes JSONL first (durable), then PG (best-effort)
- [x] File path: `/mnt/memory/agents/{agent_id}/episodic/{session_id}.jsonl`
- [x] JSONL record format: id, agent_id, timestamp, type, content, session_id, metadata
- [x] `scripts/rebuild_index.py` — replay JSONL into PG, idempotent (ON CONFLICT DO NOTHING)
- [x] Verified: store from ag-1 → JSONL on NAS + PG on workstation, same UUID in both
- [x] Fixed NAS mount permissions (uid=tgds,gid=tgds) in both `nas` and `agent-memory` Ansible roles
- [x] Fixed `tasks` → `pre_tasks` in all install playbooks (apt was running after roles)

**Completed: 2026-03-23. All Phase 2 tests passing.**

---

## Phase 3: Embeddings + Semantic Recall

- [x] `src/embeddings.py` — SentenceTransformers wrapper (nomic-embed-text-v1.5, 768-dim)
- [x] Model loaded once at server startup (~548 MB, ~10s load time)
- [x] `store_memory` generates embedding, stores in PG `memories.embedding`
- [x] `recall` uses cosine similarity (`<=>` operator) via HNSW index, falls back to recency
- [x] Similarity threshold parameter (default 0.3)
- [x] JSONL record does NOT store embeddings (re-generated on rebuild)
- [x] `rebuild_index.py` generates embeddings during replay (`--no-embeddings` to skip)
- [x] Verified: `recall("nginx configuration")` → nginx memory ranked first
- [x] Verified: `recall("what package manager")` → uv memory ranked first
- [x] Fixed: vm-services CPU type changed from qemu64 to host (required for NumPy/torch)
- [x] Fixed: secrets moved to gitignored `secrets.yml` (HF_TOKEN, PG password)
- [x] Added `einops` dependency (required by nomic model)

**Completed: 2026-03-23. All Phase 3 tests passing.**

---

## Phase 4: Fact Extraction

- [x] `src/extraction/facts.py` — FactExtractor with structured prompt → JSON
- [x] Extraction prompt → facts (list), entities (list with type), tags (list), shareable (bool)
- [x] Primary model: claude-haiku-4-5-20251001 via anthropic SDK
- [x] Fallback: Ollama via httpx (graceful skip if both fail)
- [x] On `store_memory`: extract facts, store as separate `semantic` rows in PG with embeddings
- [x] JSONL record includes `extraction` block: facts, entities, tags, model, extracted_at, shareable, status
- [x] `provenance` JSONB column populated (extraction_model, extraction_status, extracted_at)
- [x] `rebuild_index.py` uses cached extractions from JSONL (no LLM re-calls)
- [x] Verified: "Alice from Acme" → 3 facts, 3 entities, recall finds "Alice wants 1000 rpm" at 0.71 similarity
- [x] Secrets (ANTHROPIC_API_KEY) managed via gitignored secrets.yml in Ansible

**Completed: 2026-03-23. All Phase 4 tests passing.**

---

## Phase 5: Shared Memory + Promotion

- [x] `src/extraction/promotion.py` — rule-based auto-promotion (shareable tags + LLM flag)
- [x] Auto-share rules: infrastructure, configuration, deployment, networking, tools, error resolutions
- [x] Keep private: in-progress, hypothesis, debugging, draft, temporary, wip
- [x] On `store_memory`: if promoted, writes to both private and `shared/episodic/` on NAS
- [x] PG: `shared = true`, `shared_by = agent_id` on promoted memories and their extracted facts
- [x] `recall_semantic` searches `agent_id = X OR shared = TRUE` (private + shared)
- [x] `scripts/curate.py` — batch LLM review of private memories for missed promotions
- [x] Verified: ag-1 stores "dev server port 3001" → auto-promotes → ag-2 recalls at 0.81 similarity with `shared_by: ag-1`

**Completed: 2026-03-23. All Phase 5 tests passing.**

---

## Phase 6: Agent Integration

- [x] Researched OpenClaw integration: not MCP plugin-based, uses hooks system
- [x] Removed incorrect `mcp-integration` plugin config from both agents
- [x] Built `memory-sync` OpenClaw hook (`hook/memory-sync/`)
  - Listens to: `command:new`, `command:reset`, `message:sent`
  - On session end: stores conversation summary via memory server HTTP API
  - On message sent: continuous capture of agent responses
  - Config via `hooks.internal.entries.memory-sync.env` in openclaw.json
- [x] Install hook on ag-1 and ag-2 via Ansible (`memory-hook` role in agents-install.yml)
- [x] Configure hook env: `MEMORY_API_URL`, `AGENT_ID` (ag-1 and ag-2 respectively)
- [x] DHCP reservations set on Araknis router — IPs survive power outages
- [x] `scripts/init-agent-memory.sh` — bootstrap `memory/`, `MEMORY.md`, daily note, heartbeat state on agent VMs
  - Idempotent, SSH-based, accepts host list as args
  - Deployed to ag-1 and ag-2 (2026-03-24)
- [x] Fixed AGENT_ID bug: `event.context.cfg` not populated by OpenClaw; added fallback to read `openclaw.json` directly
  - Root cause: OpenClaw inlines hook code into gateway process, stripping top-level ESM imports
  - Fix: dynamic `await import("node:fs")` inside async `getConfig()` to read openclaw.json
  - Existing "unknown" records reassigned to ag-1 in PG and NAS
  - Also killed stale terminal-session OpenClaw process that was competing for Telegram polling
- [x] Test continuous capture: agent responds → verify each turn stored
  - Verified: ag-1's Telegram group messages auto-captured (6 entries from 2026-03-24)
  - Hook fires on `message:sent`, stores to JSONL + PG with fact extraction
- [x] Test cross-agent sharing: ag-1 learns → ag-2 benefits in separate session
  - Verified: ag-1 stored tool preferences → ag-2 recalled at 0.84 similarity
  - Reverse direction also works: ag-1 recalls ag-2's stack prefs at 0.75 similarity
  - Auto-promotion working: infrastructure/tooling tags → shared namespace
- [x] Test full session lifecycle: conversation → /new → verify memory stored
  - Verified: JM DM'd `/new` to Adele → new session message + greeting stored as ag-1
- [x] Session-start recall: on `/new`, hook recalls top 10 shared memories → writes `memory/recalled.md`
  - AGENTS.md updated to read `recalled.md` at startup (step 4)
  - Query: "important facts about JM preferences, tools, infrastructure, project conventions"
  - Filtered to >50% similarity to reduce noise
  - Bug fix: recall now runs even when old session has no messages
- [x] Monitor: `scripts/memory-stats.sh` — counts by agent/type, extraction stats, shared namespace, storage health, NAS inventory
  - All metrics from direct SQL queries (PG schema has rich columns)
  - `memory_status` MCP tool is health-check only (pg/nas/embedding/extraction status)

**Phase 6 complete: 2026-03-24. All items passing.**

---

## Deferred Phases

_These are future capabilities, not committed work. They are subject to redesign once we have usage data from the active phases above. Priorities and approach will evolve based on what we observe in production. Promote on demand._

### D1: BM25 Keyword Search (2nd retrieval channel) — COMPLETE

- [x] Add PostgreSQL full-text search (tsvector/tsquery) index on `memories.content`
  - Migration `002_add_bm25.sql`: GENERATED ALWAYS AS tsvector column + GIN index
  - Existing rows auto-populated at ALTER time
- [x] Second retrieval path in `recall`: keyword match alongside semantic
  - `recall_bm25()` in postgres.py using `plainto_tsquery` + `ts_rank`
  - Same visibility rules as semantic (agent_id = X OR shared = TRUE)
- [x] Reciprocal Rank Fusion to merge semantic + keyword results
  - `rrf_merge()` with k=60, documents in both channels rank highest
  - `recall()` MCP tool transparently uses hybrid — same signature
  - Recency fallback only if both channels return empty

**Completed: 2026-03-24. Verified: "XYZZY_CONFIG" exact match + "port 3001" keyword match both found via hybrid recall.**

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

### D6: Nightly Curation Cron — COMPLETE

- [x] Schedule `curate.py` as a systemd timer
  - `agent-memory-curate.timer` — daily at 03:00 UTC, Persistent=true
  - `agent-memory-curate.service` — oneshot, same user/env as main server
  - `deploy.sh` updated to enable timer on deploy
- [x] Configurable schedule (edit `OnCalendar` in timer unit)
- [x] Reports via journalctl (reviewed N, promoted N, skipped N)

**Completed: 2026-03-24. First run: reviewed 19 private memories, promoted 6.**

### D7: Web Admin UI — ABSORBED

**Absorbed into multi-agent-dashboard** (~/Code/multi-agent-dashboard, Phase 5).
Memory browser with searchable table, filters, expandable rows, extraction details — all built into the Bun + React dashboard on port 9020.

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
