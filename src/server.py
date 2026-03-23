"""Multi-agent memory MCP server."""

import logging
import uuid
from datetime import datetime, timezone

from fastmcp import FastMCP

from src.config import Config
from src.embeddings import Embedder
from src.extraction.facts import FactExtractor
from src.storage.jsonl import JSONLStorage
from src.storage.postgres import PGStorage

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

    # Extract facts
    extraction = extractor.extract(text)
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
    }

    # Write-ahead: JSONL first (durable), then PG (best-effort)
    jsonl_ok = False
    try:
        jsonl.append(record=record, agent_id=agent_id, session_id=session_id)
        jsonl_ok = True
    except OSError as e:
        log.warning(f"JSONL write failed (NAS issue): {e}")

    pg_ok = False
    try:
        # Store the episodic memory
        pg.store(
            memory_id=memory_id,
            text=text,
            agent_id=agent_id,
            session_id=session_id,
            created_at=now,
            embedding=embedding,
            provenance=provenance,
        )

        # Store extracted facts as separate semantic rows
        if extraction.facts:
            fact_embeddings = None
            try:
                fact_embeddings = [embedder.embed(f) for f in extraction.facts]
            except Exception as e:
                log.warning(f"Fact embedding failed: {e}")

            pg.store_facts(
                facts=extraction.facts,
                agent_id=agent_id,
                session_id=session_id,
                source_memory_id=memory_id,
                created_at=now,
                embeddings=fact_embeddings,
                provenance=provenance,
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
        "extraction": {
            "facts": len(extraction.facts),
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
    """Search an agent's memories by semantic similarity.

    Args:
        query: Natural language query to search for.
        agent_id: Which agent's memories to search.
        limit: Maximum number of results to return.

    Returns:
        List of matching memory records, ranked by relevance.
    """
    if not agent_id.strip():
        return [{"error": "agent_id is required"}]

    try:
        query_embedding = embedder.embed(query)
        results = pg.recall_semantic(
            query_embedding=query_embedding,
            agent_id=agent_id,
            limit=limit,
        )
        if not results:
            results = pg.recall(query=query, agent_id=agent_id, limit=limit)
        return results
    except Exception as e:
        log.warning(f"Semantic recall failed, falling back to recency: {e}")
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
