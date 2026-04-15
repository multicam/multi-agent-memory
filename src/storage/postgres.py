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

    # Public alias. Scripts and future non-test callers should use this;
    # _get_conn is kept for back-compat and the test-only mock path.
    @contextmanager
    def get_conn(self):
        """Public connection context manager. Same semantics as _get_conn."""
        with self._get_conn() as conn:
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
        subtypes: list[str] | None = None,
        conn=None,
    ) -> list[str]:
        """Store extracted facts as separate semantic memory rows. Returns IDs.

        subtypes: optional parallel list of "fact"/"decision" labels written
            into each row's provenance.subtype. Used by recall_recent_decisions
            as a structural discriminator (replaces English ILIKE matching).
        conn: optional existing connection — if given, rows are appended to
            that connection's in-flight transaction (caller owns commit).
        """
        ids = []
        shared_by = agent_id if shared else None

        def _insert_all(c):
            for i, fact in enumerate(facts):
                fact_id = str(uuid.uuid4())
                emb_str = str(embeddings[i]) if embeddings and i < len(embeddings) else None

                # Per-row provenance: merge subtype into the provided provenance dict.
                per_prov = dict(provenance or {})
                per_prov["source_memory_id"] = source_memory_id
                if subtypes and i < len(subtypes):
                    per_prov["subtype"] = subtypes[i]
                per_prov_json = json.dumps(per_prov)

                c.execute(
                    """
                    INSERT INTO memories (id, agent_id, memory_type, content, source_session, embedding, provenance, shared, shared_by, created_at, updated_at, importance, tags)
                    VALUES (%s, %s, 'semantic', %s, %s, %s::vector, %s::jsonb, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (fact_id, agent_id, fact, session_id, emb_str, per_prov_json, shared, shared_by, created_at, created_at, importance, tags),
                )
                ids.append(fact_id)

        if conn is not None:
            _insert_all(conn)
        else:
            with self._get_conn() as c:
                with c.transaction():
                    _insert_all(c)

        return ids

    def store_with_facts_and_chunks(
        self,
        memory_id: str,
        text: str,
        agent_id: str,
        session_id: str,
        created_at: datetime,
        embedding: list[float] | None = None,
        provenance: dict | None = None,
        shared: bool = False,
        importance: float = 0.5,
        tags: list[str] | None = None,
        facts: list[str] | None = None,
        decisions: list[str] | None = None,
        fact_embeddings: list[list[float]] | None = None,
        chunks: list[str] | None = None,
        chunk_embeddings: list[list[float]] | None = None,
    ) -> None:
        """Write an episodic row + its semantic facts/decisions + text chunks
        in a single transaction, closing the partial-failure window flagged
        in the 2026-04-15 review (P1 write-ahead softness).

        Either the whole tree commits or nothing does. Chunks carry
        provenance={parent_memory_id, chunk: True}; semantic rows carry
        provenance.subtype='fact' or 'decision' for structural filtering.
        """
        emb_str = str(embedding) if embedding else None
        prov_json = json.dumps(provenance) if provenance else None
        shared_by = agent_id if shared else None

        facts = facts or []
        decisions = decisions or []
        all_semantic = facts + decisions
        subtypes = (["fact"] * len(facts)) + (["decision"] * len(decisions))

        chunks = chunks or []

        with self._get_conn() as conn:
            with conn.transaction():
                # 1. Episodic row
                conn.execute(
                    """
                    INSERT INTO memories (id, agent_id, memory_type, content, source_session, embedding, provenance, shared, shared_by, created_at, updated_at, importance, tags)
                    VALUES (%s, %s, 'episodic', %s, %s, %s::vector, %s::jsonb, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (memory_id, agent_id, text, session_id, emb_str, prov_json, shared, shared_by, created_at, created_at, importance, tags),
                )

                # 2. Semantic rows (facts + decisions) — share the same conn+tx
                if all_semantic:
                    self.store_facts(
                        facts=all_semantic,
                        agent_id=agent_id,
                        session_id=session_id,
                        source_memory_id=memory_id,
                        created_at=created_at,
                        embeddings=fact_embeddings,
                        provenance=provenance,
                        shared=shared,
                        importance=importance,
                        tags=tags,
                        subtypes=subtypes,
                        conn=conn,
                    )

                # 3. Chunks — each gets its own id but shares the parent provenance
                for i, chunk in enumerate(chunks):
                    chunk_id = str(uuid.uuid4())
                    chunk_emb = None
                    if chunk_embeddings and i < len(chunk_embeddings) and chunk_embeddings[i] is not None:
                        chunk_emb = str(chunk_embeddings[i])
                    chunk_prov = {"parent_memory_id": memory_id, "chunk": True}
                    conn.execute(
                        """
                        INSERT INTO memories (id, agent_id, memory_type, content, source_session, embedding, provenance, shared, shared_by, created_at, updated_at, importance, tags)
                        VALUES (%s, %s, 'episodic', %s, %s, %s::vector, %s::jsonb, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (chunk_id, agent_id, chunk, session_id, chunk_emb, json.dumps(chunk_prov), shared, shared_by, created_at, created_at, 0.0, tags),
                    )

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
                WITH qv AS (SELECT %s::vector AS vec)
                SELECT id, agent_id, memory_type, content, source_session, shared, shared_by, created_at,
                       1 - (embedding <=> qv.vec) AS similarity
                FROM memories, qv
                WHERE (agent_id = %s OR shared = TRUE)
                  AND embedding IS NOT NULL
                  AND 1 - (embedding <=> qv.vec) > %s
                ORDER BY embedding <=> qv.vec
                LIMIT %s
                """,
                (qe, agent_id, threshold, limit),
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

        Uses a CTE to bind the query vector once, then ORDER BY + LIMIT 1
        to leverage the HNSW index (not WHERE on computed similarity).
        """
        qe = str(embedding)
        with self._get_conn() as conn:
            row = conn.execute(
                """WITH qv AS (SELECT %s::vector AS vec)
                   SELECT id, 1 - (embedding <=> qv.vec) AS similarity
                   FROM memories, qv
                   WHERE agent_id = %s AND embedding IS NOT NULL
                   ORDER BY embedding <=> qv.vec
                   LIMIT 1""",
                (qe, agent_id),
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
        """Recent decisions with rationale.

        Prefers the structural `provenance.subtype = 'decision'` discriminator
        written at store-time. Falls back to English ILIKE matching for legacy
        rows written before the subtype was introduced (2026-04-15 review P1).
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, agent_id, memory_type, content, source_session, shared, shared_by, created_at
                FROM memories
                WHERE memory_type = 'semantic'
                  AND (agent_id = %s OR shared = TRUE)
                  AND (
                        provenance->>'subtype' = 'decision'
                        OR (
                            provenance->>'subtype' IS NULL
                            AND (content ILIKE '%%decided%%' OR content ILIKE '%%because%%' OR content ILIKE '%%chose%%')
                        )
                      )
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
