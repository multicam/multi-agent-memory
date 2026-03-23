"""Multi-agent memory MCP server."""

import os

from fastmcp import FastMCP

from src.config import Config
from src.storage.postgres import PGStorage

config = Config.from_env()
pg = PGStorage(config.pg_url)

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

    try:
        return pg.store(text=text, agent_id=agent_id, session_id=session_id)
    except Exception as e:
        return {"error": str(e)}


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
    nas_mounted = os.path.ismount(config.nas_path)

    return {
        "pg": "connected" if pg.is_connected() else "disconnected",
        "nas": "mounted" if nas_mounted else "unmounted",
        "nas_path": config.nas_path,
    }


def main():
    pg.connect()
    print(f"Connected to PostgreSQL")
    print(f"NAS path: {config.nas_path} (mounted: {os.path.ismount(config.nas_path)})")
    print(f"Starting MCP server on {config.server_host}:{config.server_port}")
    mcp.run(transport="streamable-http", host=config.server_host, port=config.server_port)


if __name__ == "__main__":
    main()
