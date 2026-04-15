"""Multi-agent memory MCP server."""

import logging
import uuid
from datetime import datetime, timezone

from fastmcp import FastMCP

from src.config import Config
from src.embeddings import Embedder
from src.extraction.facts import FactExtractor
from src.extraction.importance import score_importance
from src.extraction.promotion import should_promote
from src.storage.jsonl import JSONLStorage
from src.storage.postgres import PGStorage, rrf_merge

log = logging.getLogger("agent-memory")

_CHUNK_SIZE = 800
_CHUNK_OVERLAP = 100


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Split text into overlapping chunks.

    Raises ValueError if overlap >= size (would loop forever).
    Drops trailing chunks that are no larger than the overlap, since
    they contain no new information beyond what the previous chunk covers.
    """
    if overlap >= size:
        raise ValueError(f"overlap ({overlap}) must be < size ({size})")
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunk = text[start:end]
        if len(chunk) <= overlap and chunks:
            break  # tail is fully covered by previous chunk's overlap
        chunks.append(chunk)
        start = end - overlap
    return chunks

# Lazy-initialised module globals. Populated by _init_state() (called by main()
# or lazily by each tool). Import-time has zero env reads and no object
# construction -- the single most load-bearing requirement fixed in the
# 2026-04-15 review (P0: module-level side effects).
config: Config | None = None
pg: PGStorage | None = None
jsonl: JSONLStorage | None = None
embedder: Embedder | None = None
extractor: FactExtractor | None = None


def _init_state(need_jsonl: bool = True, need_extractor: bool = True) -> None:
    """Populate module globals from env. Idempotent.

    Only initialises globals that are still None *and* requested by the
    caller (per-tool flags). Tests that monkeypatch individual globals
    with mocks aren't forced to supply env vars for the ones they don't
    touch. This is both the lazy-init entrypoint (via _ensure_ready) and
    the explicit startup hook (called by main()).
    """
    global config, pg, jsonl, embedder, extractor
    # Early exit: nothing missing that the caller wants -- keeps import-time
    # and per-call costs at zero when mocks are already installed.
    if (
        pg is not None
        and embedder is not None
        and (not need_jsonl or jsonl is not None)
        and (not need_extractor or extractor is not None)
    ):
        return
    if config is None:
        config = Config.from_env()
    if pg is None:
        pg = PGStorage(config.pg_url)
    if need_jsonl and jsonl is None:
        jsonl = JSONLStorage(config.nas_path)
    if embedder is None:
        embedder = Embedder()
    if need_extractor and extractor is None:
        extractor = FactExtractor(
            api_key=config.anthropic_api_key,
            ollama_base_url=config.ollama_base_url,
        )


# Alias -- kept for readability at the tool call sites.
_ensure_ready = _init_state


mcp = FastMCP(
    "multi-agent-memory",
    instructions="Shared memory system for AI agents. Use store_memory to save, recall to search, memory_status to check health.",
)


@mcp.tool()
def store_memory(text: str, agent_id: str, session_id: str) -> dict:
    """Store a memory for an agent. Extracts facts and generates embeddings automatically.

    Args:
        text: The memory content to store.
        agent_id: Which agent is storing this memory (e.g. "ag-1").
        session_id: Current session identifier.

    Returns:
        The stored memory record with its ID and extraction results.
    """
    if not text.strip():
        return {"error": "text cannot be empty"}
    if not agent_id.strip():
        return {"error": "agent_id is required"}

    _ensure_ready()
    memory_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Generate embedding
    embedding = None
    try:
        embedding = embedder.embed(text)
    except Exception as e:
        log.warning(f"Embedding generation failed: {e}")

    # Dedup check (best-effort -- skipped if PG unavailable or no embedding)
    if embedding:
        try:
            existing_id = pg.check_duplicate(embedding, agent_id)
            if existing_id:
                return {"status": "duplicate", "existing_id": existing_id}
        except Exception:
            pass  # PG down -- skip dedup, preserve write-ahead guarantee

    # Extract facts and determine promotion
    extraction = extractor.extract(text)
    importance = score_importance(text, extraction)
    promoted = should_promote(extraction)
    provenance = {
        "extraction_model": extraction.model,
        "extraction_status": extraction.status,
        "extracted_at": extraction.extracted_at,
    }

    # Build JSONL record (includes extraction, excludes embeddings)
    record = {
        "id": memory_id,
        "agent_id": agent_id,
        "timestamp": now.isoformat(),
        "type": "episodic",
        "content": text,
        "session_id": session_id,
        "metadata": {},
        "extraction": extraction.to_dict(),
        "promoted": promoted,
    }

    # Write-ahead: JSONL first (durable), then PG (best-effort)
    jsonl_ok = False
    try:
        jsonl.append(record=record, agent_id=agent_id, session_id=session_id)
        if promoted:
            jsonl.append_shared(record=record, session_id=session_id)
        jsonl_ok = True
    except OSError as e:
        log.warning(f"JSONL write failed (NAS issue): {e}")

    pg_ok = False
    try:
        # Pre-compute semantic embeddings (best-effort -- parse failure is non-fatal)
        all_semantic = extraction.facts + extraction.decisions
        sem_embeddings = None
        if all_semantic:
            try:
                sem_embeddings = embedder.embed_batch(all_semantic)
            except Exception as e:
                log.warning(f"Semantic embedding failed: {e}")

        # Pre-compute chunks + chunk embeddings outside the transaction so an
        # embedder failure doesn't roll back the main insert.
        chunks: list[str] = []
        chunk_embeddings: list[list[float] | None] = []
        if len(text) > _CHUNK_SIZE and embedding:
            chunks = _chunk_text(text, _CHUNK_SIZE, _CHUNK_OVERLAP)
            try:
                chunk_embeddings = embedder.embed_batch(chunks)
            except Exception as e:
                log.warning(f"Chunk embedding failed: {e}")
                chunk_embeddings = [None] * len(chunks)

        # Single transaction for episodic + semantic + chunks. Fixes the
        # partial-failure window flagged in the 2026-04-15 review (P1).
        pg.store_with_facts_and_chunks(
            memory_id=memory_id,
            text=text,
            agent_id=agent_id,
            session_id=session_id,
            created_at=now,
            embedding=embedding,
            provenance=provenance,
            shared=promoted,
            importance=importance,
            tags=extraction.tags or None,
            facts=extraction.facts,
            decisions=extraction.decisions,
            fact_embeddings=sem_embeddings,
            chunks=chunks,
            chunk_embeddings=chunk_embeddings,
        )

        pg_ok = True
    except Exception as e:
        log.warning(f"PG write failed: {e}")

    if not jsonl_ok and not pg_ok:
        return {"error": "Both JSONL and PG writes failed"}

    return {
        "id": memory_id,
        "agent_id": agent_id,
        "memory_type": "episodic",
        "session_id": session_id,
        "created_at": now.isoformat(),
        "promoted": promoted,
        "importance": round(importance, 2),
        "extraction": {
            "facts": len(extraction.facts),
            "decisions": len(extraction.decisions),
            "entities": len(extraction.entities),
            "tags": extraction.tags,
            "shareable": extraction.shareable,
            "status": extraction.status,
        },
        "storage": {
            "jsonl": "ok" if jsonl_ok else "failed",
            "pg": "ok" if pg_ok else "failed",
        },
    }


@mcp.tool()
def recall(query: str, agent_id: str, limit: int = 10) -> list[dict]:
    """Search an agent's memories by hybrid semantic + keyword matching. Includes shared memories from other agents.

    Args:
        query: Natural language query to search for.
        agent_id: Which agent's memories to search.
        limit: Maximum number of results to return.

    Returns:
        List of matching memory records, ranked by relevance (RRF fusion of semantic + BM25).
    """
    if not agent_id.strip():
        return [{"error": "agent_id is required"}]

    _ensure_ready(need_jsonl=False, need_extractor=False)
    semantic_results = []
    bm25_results = []

    # Channel 1: Semantic (embedding similarity)
    try:
        query_embedding = embedder.embed(query)
        semantic_results = pg.recall_semantic(
            query_embedding=query_embedding,
            agent_id=agent_id,
            limit=limit,
        )
    except Exception as e:
        log.warning(f"Semantic recall failed: {e}")

    # Channel 2: BM25 (keyword match)
    try:
        bm25_results = pg.recall_bm25(
            query=query,
            agent_id=agent_id,
            limit=limit,
        )
    except Exception as e:
        log.warning(f"BM25 recall failed: {e}")

    # Merge via RRF if we have results from either channel
    if semantic_results or bm25_results:
        return rrf_merge(semantic_results, bm25_results, limit=limit)

    # Last resort: recency fallback
    log.warning("Both semantic and BM25 recall failed, falling back to recency")
    return pg.recall(query=query, agent_id=agent_id, limit=limit)


@mcp.tool()
def wake_up(agent_id: str) -> dict:
    """Load structured memory layers for session start.

    Returns importance-ranked critical memories and recent decisions
    to bootstrap an agent's context at the beginning of a session.

    Args:
        agent_id: Which agent is waking up.

    Returns:
        Layered memory context with token estimate.
    """
    if not agent_id.strip():
        return {"error": "agent_id is required"}

    _ensure_ready(need_jsonl=False, need_extractor=False)
    layer_1: list[dict] = []
    layer_2: list[dict] = []

    try:
        layer_1 = pg.recall_important(agent_id, limit=8)
    except Exception as e:
        log.warning(f"recall_important failed: {e}")

    try:
        layer_2 = pg.recall_recent_decisions(agent_id, limit=5)
    except Exception as e:
        log.warning(f"recall_recent_decisions failed: {e}")

    return {
        "layer_1_critical": layer_1,
        "layer_2_decisions": layer_2,
        "token_estimate": sum(len(m.get("content", "")) // 4 for m in layer_1 + layer_2),
    }


@mcp.tool()
def memory_status() -> dict:
    """Check the health of the memory system.

    Returns:
        Status of PostgreSQL connection, NAS mount, and embedding model.
    """
    _ensure_ready()
    return {
        "pg": "connected" if pg.is_connected() else "disconnected",
        "nas": "mounted" if jsonl.is_mounted() else "unmounted",
        "nas_path": config.nas_path,
        "embedding_model": embedder.model_name,
        "extraction": "haiku" if config.anthropic_api_key else ("ollama" if config.ollama_base_url else "disabled"),
    }


def main():
    _init_state()
    pg.connect()
    embedder.load()
    print(f"Connected to PostgreSQL")
    print(f"NAS path: {config.nas_path} (mounted: {jsonl.is_mounted()})")
    print(f"Embedding model: {embedder.model_name} ({embedder.dimensions}-dim)")
    print(f"Extraction: {'haiku' if config.anthropic_api_key else 'disabled (no ANTHROPIC_API_KEY)'}")
    print(f"Starting MCP server on {config.server_host}:{config.server_port}")
    mcp.run(transport="streamable-http", host=config.server_host, port=config.server_port)


if __name__ == "__main__":
    main()
