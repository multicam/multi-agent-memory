"""PostgreSQL storage layer with connection pooling."""

import json
import uuid
from contextlib import contextmanager
from datetime import datetime

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def _format_row(row: dict, extra_fields: tuple[str, ...] = ()) -> dict:
    """Format a DB row into a memory dict with standard + extra fields."""
    result = {
        "id": str(row["id"]),
        "agent_id": row["agent_id"],
        "memory_type": row["memory_type"],
        "content": row["content"],
        "session_id": row["source_session"],
        "shared": row["shared"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
    if "shared_by" in row:
        result["shared_by"] = row["shared_by"]
    for field in extra_fields:
        if field in row:
            result[field] = round(float(row[field]), 4)
    return result


class PGStorage:
    def __init__(self, pg_url: str):
        self._pg_url = pg_url
        self._pool: ConnectionPool | None = None
        # Direct connection for test mocks and single-threaded scripts.
        # When set, _get_conn() yields this instead of acquiring from pool.
        self._conn: psycopg.Connection | None = None

    def connect(self) -> None:
        self._pool = ConnectionPool(
            self._pg_url,
            min_size=2,
            max_size=10,
            kwargs={"row_factory": dict_row},
        )

    def close(self) -> None:
        if self._pool:
            self._pool.close()
            self._pool = None
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def _get_conn(self):
        """Yield a connection — mock/direct if set, otherwise from pool."""
        if self._conn is not None:
            yield self._conn
            return
        if not self._pool:
            raise RuntimeError("Not connected to PostgreSQL")
        with self._pool.connection() as conn:
            yield conn

    def is_connected(self) -> bool:
        try:
            with self._get_conn() as conn:
                conn.execute("SELECT 1")
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
        importance: float = 0.5,
        tags: list[str] | None = None,
    ) -> None:
        """Insert a memory row. Raises on failure."""
        emb_str = str(embedding) if embedding else None
        prov_json = json.dumps(provenance) if provenance else None
        shared_by = agent_id if shared else None

        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO memories (id, agent_id, memory_type, content, source_session, embedding, provenance, shared, shared_by, created_at, updated_at, importance, tags)
                VALUES (%s, %s, %s, %s, %s, %s::vector, %s::jsonb, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (memory_id, agent_id, memory_type, text, session_id, emb_str, prov_json, shared, shared_by, created_at, created_at, importance, tags),
            )
            conn.commit()

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
        importance: float = 0.5,
        tags: list[str] | None = None,
    ) -> list[str]:
        """Store extracted facts as separate semantic memory rows. Returns IDs."""
        ids = []
        prov_json = json.dumps(provenance) if provenance else None
        shared_by = agent_id if shared else None

        with self._get_conn() as conn:
            with conn.transaction():
                for i, fact in enumerate(facts):
                    fact_id = str(uuid.uuid4())
                    emb_str = str(embeddings[i]) if embeddings and i < len(embeddings) else None

                    conn.execute(
                        """
                        INSERT INTO memories (id, agent_id, memory_type, content, source_session, embedding, provenance, shared, shared_by, created_at, updated_at, importance, tags)
                        VALUES (%s, %s, 'semantic', %s, %s, %s::vector, %s::jsonb, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (fact_id, agent_id, fact, session_id, emb_str, prov_json, shared, shared_by, created_at, created_at, importance, tags),
                    )
                    ids.append(fact_id)

        return ids

    def recall_semantic(
        self,
        query_embedding: list[float],
        agent_id: str,
        limit: int = 10,
        threshold: float = 0.3,
    ) -> list[dict]:
        """Recall memories by cosine similarity. Searches agent's own + shared memories."""
        qe = str(query_embedding)
        with self._get_conn() as conn:
            rows = conn.execute(
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

        return [_format_row(r, extra_fields=("similarity",)) for r in rows]

    def recall_bm25(
        self,
        query: str,
        agent_id: str,
        limit: int = 10,
    ) -> list[dict]:
        """Recall memories by BM25 full-text search. Searches agent's own + shared memories."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, agent_id, memory_type, content, source_session, shared, shared_by, created_at,
                       ts_rank(search_vector, plainto_tsquery('english', %s)) AS bm25_rank
                FROM memories
                WHERE (agent_id = %s OR shared = TRUE)
                  AND search_vector @@ plainto_tsquery('english', %s)
                ORDER BY bm25_rank DESC
                LIMIT %s
                """,
                (query, agent_id, query, limit),
            ).fetchall()

        return [_format_row(r, extra_fields=("bm25_rank",)) for r in rows]

    def recall(
        self,
        query: str,
        agent_id: str,
        limit: int = 10,
    ) -> list[dict]:
        """Recency-based recall (fallback when no embedding available)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, agent_id, memory_type, content, source_session, shared, shared_by, created_at
                FROM memories
                WHERE (agent_id = %s OR shared = TRUE)
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (agent_id, limit),
            ).fetchall()

        return [_format_row(r) for r in rows]

    def check_duplicate(
        self,
        embedding: list[float],
        agent_id: str,
        threshold: float = 0.92,
    ) -> str | None:
        """Return existing memory ID if near-duplicate found, else None.

        Uses ORDER BY + LIMIT 1 to leverage HNSW index (not WHERE on computed similarity).
        """
        qe = str(embedding)
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT id, 1 - (embedding <=> %s::vector) AS similarity
                   FROM memories
                   WHERE agent_id = %s AND embedding IS NOT NULL
                   ORDER BY embedding <=> %s::vector
                   LIMIT 1""",
                (qe, agent_id, qe),
            ).fetchone()
        if row and float(row["similarity"]) > threshold:
            return str(row["id"])
        return None

    def recall_important(
        self,
        agent_id: str,
        limit: int = 8,
    ) -> list[dict]:
        """Top memories by importance score. Excludes chunks (importance=0)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, agent_id, memory_type, content, source_session, shared, shared_by, created_at
                FROM memories
                WHERE (agent_id = %s OR shared = TRUE)
                  AND importance > 0
                ORDER BY importance DESC, created_at DESC
                LIMIT %s
                """,
                (agent_id, limit),
            ).fetchall()
        return [_format_row(r) for r in rows]

    def recall_recent_decisions(
        self,
        agent_id: str,
        limit: int = 5,
    ) -> list[dict]:
        """Recent decisions with rationale."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, agent_id, memory_type, content, source_session, shared, shared_by, created_at
                FROM memories
                WHERE memory_type = 'semantic'
                  AND provenance->>'extraction_status' IS NOT NULL
                  AND (content ILIKE '%%decided%%' OR content ILIKE '%%because%%' OR content ILIKE '%%chose%%')
                  AND (agent_id = %s OR shared = TRUE)
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (agent_id, limit),
            ).fetchall()
        return [_format_row(r) for r in rows]

    def count(self) -> int:
        with self._get_conn() as conn:
            row = conn.execute("SELECT count(*) AS n FROM memories").fetchone()
        return row["n"]

    def truncate(self) -> None:
        with self._get_conn() as conn:
            conn.execute("TRUNCATE memories")
            conn.commit()


def rrf_merge(
    semantic_results: list[dict],
    bm25_results: list[dict],
    k: int = 60,
    limit: int = 10,
) -> list[dict]:
    """Merge two ranked result lists using Reciprocal Rank Fusion.

    RRF score = sum(1 / (k + rank)) for each list the document appears in.
    k=60 is the standard value from Cormack et al. 2009.
    """
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}

    for rank, doc in enumerate(semantic_results):
        doc_id = doc["id"]
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
        docs[doc_id] = doc

    for rank, doc in enumerate(bm25_results):
        doc_id = doc["id"]
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
        if doc_id not in docs:
            docs[doc_id] = doc

    sorted_ids = sorted(scores.keys(), key=lambda did: scores[did], reverse=True)

    results = []
    for doc_id in sorted_ids[:limit]:
        doc = docs[doc_id].copy()
        doc["rrf_score"] = round(scores[doc_id], 6)
        results.append(doc)

    return results
