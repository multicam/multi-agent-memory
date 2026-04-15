# Changelog

All notable changes to the multi-agent-memory MCP server.

## Unreleased (on `deps/bump-2026-04`)

### Fixed
- **P0** Module-level side effects in `src/server.py` — `Config.from_env()`,
  `PGStorage`, `Embedder`, `FactExtractor` are no longer constructed at
  import time. `python -c "import src.server"` now succeeds without
  `PG_URL` set.
- **P0** `scripts/reconcile_jsonl.py` now cross-checks semantic row counts
  per parent episodic. Silent facts/decisions drift surfaces as DIVERGED
  instead of being hidden behind a "HEALTHY" label.
- **P1** Write-ahead softness: episodic + semantic (facts + decisions) +
  chunk rows now write in a single `conn.transaction()` via
  `PGStorage.store_with_facts_and_chunks()`.
- **P1** Decision discovery no longer relies on English ILIKE matching.
  `recall_recent_decisions` prefers `provenance->>'subtype' = 'decision'`;
  ILIKE stays only as a fallback for legacy rows.
- **P1** `FactExtractor` JSON parse failures surface as
  `Extraction.status = "parse_error"` (was silently reporting "success"
  with empty facts).

### Changed
- `Embedder.embed_batch(texts)` added for single-encode-call batch
  inference. `server.store_memory` uses it for semantic + chunk embeddings.
- `JSONLStorage.is_writable()` added; `is_mounted()` kept as legacy alias.
  Fixes the bind-mount / symlink / subfolder-dev false negatives flagged
  in the review.
- `PGStorage.get_conn()` public alias for the private `_get_conn` hatch.
  `scripts/curate.py` updated.
- `FactExtractor` normalises tags (`_`/space → `-`, lowercase, strip)
  inside `_parse_json` so promotion rules don't need defensive casing.
- Startup now fails loud on unapplied migrations via `_check_schema`.

### Hygiene
- `main.py` at repo root deleted (leftover `uv init` scaffolding).
- `pyproject.toml` description updated; `coverage.fail_under` raised from
  0 to 70.
- `CHANGELOG.md` added.
- `scripts/rebuild_index.py` now uses `psycopg.errors.UniqueViolation`
  explicitly instead of string-matching the error message.

## 2026-04-15 — `feb6ac6`

### Dependencies
- anthropic 0.86 → 0.95
- fastmcp 3.1.1 → 3.2.4
- sentence-transformers 5.3.0 → 5.4.1
- pytest 9.0.2 → 9.0.3
- pytest-cov spec `>=6.0` → `>=7.1.0`
- Transitive: torch 2.10 → 2.11, transformers 5.3 → 5.5.4,
  pydantic 2.12.5 → 2.13.0. CUDA wheels re-rolled cu12 → cu13.
- `uv run pytest` green at 188 passed.

## 2026-04 — `3fc6142`

### Infrastructure
- Infra work tuning (connection pool tuning, deploy script polish).

## 2026-04 — `d0602e4`

### Added
- Hybrid recall (semantic + BM25 + RRF fusion), wake_up tool,
  promotion, curation — "the whole enchillada" initial integration.

## 2026-04 — `ec31e8f`

### Fixed
- Connection pooling, thread safety, hardcoded credentials, curate-path
  transactional integrity.

## 2026-04 — `f1afa1d`

### Added
- Hybrid recall + decisions tests. Coverage 72% → 77%.
