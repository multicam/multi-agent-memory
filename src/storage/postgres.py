"""PostgreSQL storage layer."""

import json
import uuid
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
        embedding: list[float] | None = None,
        provenance: dict | None = None,
        shared: bool = False,
    ) -> None:
        """Insert a memory row. Raises on failure."""
        if not self._conn:
            raise RuntimeError("Not connected to PostgreSQL")

        emb_str = str(embedding) if embedding else None
        prov_json = json.dumps(provenance) if provenance else None
        shared_by = agent_id if shared else None

        self._conn.execute(
            """
            INSERT INTO memories (id, agent_id, memory_type, content, source_session, embedding, provenance, shared, shared_by, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s::vector, %s::jsonb, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (memory_id, agent_id, memory_type, text, session_id, emb_str, prov_json, shared, shared_by, created_at, created_at),
        )
        self._conn.commit()

    def store_facts(
        self,
        facts: list[str],
        agent_id: str,
        session_id: str,
        source_memory_id: str,
        created_at: datetime,
        embeddings: list[list[float]] | None = None,
        provenance: dict | None = None,
        shared: bool = False,
    ) -> list[str]:
        """Store extracted facts as separate semantic memory rows. Returns IDs."""
        if not self._conn:
            raise RuntimeError("Not connected to PostgreSQL")

        ids = []
        prov_json = json.dumps(provenance) if provenance else None
        shared_by = agent_id if shared else None

        for i, fact in enumerate(facts):
            fact_id = str(uuid.uuid4())
            emb_str = str(embeddings[i]) if embeddings and i < len(embeddings) else None

            self._conn.execute(
                """
                INSERT INTO memories (id, agent_id, memory_type, content, source_session, embedding, provenance, shared, shared_by, created_at, updated_at)
                VALUES (%s, %s, 'semantic', %s, %s, %s::vector, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (fact_id, agent_id, fact, session_id, emb_str, prov_json, shared, shared_by, created_at, created_at),
            )
            ids.append(fact_id)

        self._conn.commit()
        return ids

    def recall_semantic(
        self,
        query_embedding: list[float],
        agent_id: str,
        limit: int = 10,
        threshold: float = 0.3,
    ) -> list[dict]:
        """Recall memories by cosine similarity. Searches agent's own + shared memories."""
        if not self._conn:
            raise RuntimeError("Not connected to PostgreSQL")

        qe = str(query_embedding)
        rows = self._conn.execute(
            """
            SELECT id, agent_id, memory_type, content, source_session, shared, shared_by, created_at,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM memories
            WHERE (agent_id = %s OR shared = TRUE)
              AND embedding IS NOT NULL
              AND 1 - (embedding <=> %s::vector) > %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (qe, agent_id, qe, threshold, qe, limit),
        ).fetchall()

        return [
            {
                "id": str(r["id"]),
                "agent_id": r["agent_id"],
                "memory_type": r["memory_type"],
                "content": r["content"],
                "session_id": r["source_session"],
                "shared": r["shared"],
                "shared_by": r["shared_by"],
                "similarity": round(float(r["similarity"]), 4),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]

    def recall(
        self,
        query: str,
        agent_id: str,
        limit: int = 10,
    ) -> list[dict]:
        """Recency-based recall (fallback when no embedding available)."""
        if not self._conn:
            raise RuntimeError("Not connected to PostgreSQL")

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
