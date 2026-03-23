"""PostgreSQL storage layer."""

from datetime import datetime

import psycopg
from psycopg.rows import dict_row


class PGStorage:
    def __init__(self, pg_url: str):
        self._pg_url = pg_url
        self._conn: psycopg.Connection | None = None

    def connect(self) -> None:
        self._conn = psycopg.connect(self._pg_url, row_factory=dict_row)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def is_connected(self) -> bool:
        if not self._conn:
            return False
        try:
            self._conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def store(
        self,
        memory_id: str,
        text: str,
        agent_id: str,
        session_id: str,
        created_at: datetime,
        memory_type: str = "episodic",
    ) -> None:
        """Insert a memory row. Raises on failure."""
        if not self._conn:
            raise RuntimeError("Not connected to PostgreSQL")

        self._conn.execute(
            """
            INSERT INTO memories (id, agent_id, memory_type, content, source_session, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (memory_id, agent_id, memory_type, text, session_id, created_at, created_at),
        )
        self._conn.commit()

    def recall(
        self,
        query: str,
        agent_id: str,
        limit: int = 10,
    ) -> list[dict]:
        if not self._conn:
            raise RuntimeError("Not connected to PostgreSQL")

        # Phase 1-2: recency-based recall only (semantic search in Phase 3)
        rows = self._conn.execute(
            """
            SELECT id, agent_id, memory_type, content, source_session, shared, shared_by, created_at
            FROM memories
            WHERE agent_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (agent_id, limit),
        ).fetchall()

        return [
            {
                "id": str(r["id"]),
                "agent_id": r["agent_id"],
                "memory_type": r["memory_type"],
                "content": r["content"],
                "session_id": r["source_session"],
                "shared": r["shared"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]

    def count(self) -> int:
        if not self._conn:
            raise RuntimeError("Not connected to PostgreSQL")
        row = self._conn.execute("SELECT count(*) AS n FROM memories").fetchone()
        return row["n"]

    def truncate(self) -> None:
        if not self._conn:
            raise RuntimeError("Not connected to PostgreSQL")
        self._conn.execute("TRUNCATE memories")
        self._conn.commit()
