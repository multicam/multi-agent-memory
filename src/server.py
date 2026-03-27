"""Multi-agent memory MCP server."""

import logging
import uuid
from datetime import datetime, timezone

from fastmcp import FastMCP

from src.config import Config
from src.embeddings import Embedder
from src.extraction.facts import FactExtractor
from src.extraction.promotion import should_promote
from src.storage.jsonl import JSONLStorage
from src.storage.postgres import PGStorage, rrf_merge

log = logging.getLogger("agent-memory")

config = Config.from_env()
pg = PGStorage(config.pg_url)
jsonl = JSONLStorage(config.nas_path)
embedder = Embedder()
extractor = FactExtractor(
    api_key=config.anthropic_api_key,
    ollama_base_url=config.ollama_base_url,
)

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

    memory_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Generate embedding
    embedding = None
    try:
        embedding = embedder.embed(text)
    except Exception as e:
        log.warning(f"Embedding generation failed: {e}")

    # Extract facts and determine promotion
    extraction = extractor.extract(text)
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
        pg.store(
            memory_id=memory_id,
            text=text,
            agent_id=agent_id,
            session_id=session_id,
            created_at=now,
            embedding=embedding,
            provenance=provenance,
            shared=promoted,
        )

        # Store extracted facts and decisions as separate semantic memories
        all_semantic = extraction.facts + extraction.decisions
        if all_semantic:
            sem_embeddings = None
            try:
                sem_embeddings = [embedder.embed(s) for s in all_semantic]
            except Exception as e:
                log.warning(f"Semantic embedding failed: {e}")

            pg.store_facts(
                facts=all_semantic,
                agent_id=agent_id,
                session_id=session_id,
                source_memory_id=memory_id,
                created_at=now,
                embeddings=sem_embeddings,
                provenance=provenance,
                shared=promoted,
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
def memory_status() -> dict:
    """Check the health of the memory system.

    Returns:
        Status of PostgreSQL connection, NAS mount, and embedding model.
    """
    return {
        "pg": "connected" if pg.is_connected() else "disconnected",
        "nas": "mounted" if jsonl.is_mounted() else "unmounted",
        "nas_path": config.nas_path,
        "embedding_model": embedder.model_name,
        "extraction": "haiku" if config.anthropic_api_key else ("ollama" if config.ollama_base_url else "disabled"),
    }


def main():
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
