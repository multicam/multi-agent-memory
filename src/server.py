"""Multi-agent memory MCP server."""

import logging
import os
import uuid
from datetime import datetime, timezone

from fastmcp import FastMCP

from src.config import Config
from src.storage.jsonl import JSONLStorage
from src.storage.postgres import PGStorage

log = logging.getLogger("agent-memory")

config = Config.from_env()
pg = PGStorage(config.pg_url)
jsonl = JSONLStorage(config.nas_path)

mcp = FastMCP(
    "multi-agent-memory",
    instructions="Shared memory system for AI agents. Use store_memory to save, recall to search, memory_status to check health.",
)


@mcp.tool()
def store_memory(text: str, agent_id: str, session_id: str) -> dict:
    """Store a memory for an agent.

    Args:
        text: The memory content to store.
        agent_id: Which agent is storing this memory (e.g. "ag-1").
        session_id: Current session identifier.

    Returns:
        The stored memory record with its ID.
    """
    if not text.strip():
        return {"error": "text cannot be empty"}
    if not agent_id.strip():
        return {"error": "agent_id is required"}

    memory_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    record = {
        "id": memory_id,
        "agent_id": agent_id,
        "timestamp": now.isoformat(),
        "type": "episodic",
        "content": text,
        "session_id": session_id,
        "metadata": {},
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
        pg.store(
            memory_id=memory_id,
            text=text,
            agent_id=agent_id,
            session_id=session_id,
            created_at=now,
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
        "storage": {
            "jsonl": "ok" if jsonl_ok else "failed",
            "pg": "ok" if pg_ok else "failed",
        },
    }


@mcp.tool()
def recall(query: str, agent_id: str, limit: int = 10) -> list[dict]:
    """Search an agent's memories.

    Args:
        query: What to search for (used for semantic search in later phases).
        agent_id: Which agent's memories to search.
        limit: Maximum number of results to return.

    Returns:
        List of matching memory records, most recent first.
    """
    if not agent_id.strip():
        return [{"error": "agent_id is required"}]

    try:
        return pg.recall(query=query, agent_id=agent_id, limit=limit)
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
def memory_status() -> dict:
    """Check the health of the memory system.

    Returns:
        Status of PostgreSQL connection and NAS mount.
    """
    return {
        "pg": "connected" if pg.is_connected() else "disconnected",
        "nas": "mounted" if jsonl.is_mounted() else "unmounted",
        "nas_path": config.nas_path,
    }


def main():
    pg.connect()
    log.info("Connected to PostgreSQL")
    log.info(f"NAS path: {config.nas_path} (mounted: {jsonl.is_mounted()})")
    log.info(f"Starting MCP server on {config.server_host}:{config.server_port}")
    print(f"Connected to PostgreSQL")
    print(f"NAS path: {config.nas_path} (mounted: {jsonl.is_mounted()})")
    print(f"Starting MCP server on {config.server_host}:{config.server_port}")
    mcp.run(transport="streamable-http", host=config.server_host, port=config.server_port)


if __name__ == "__main__":
    main()
