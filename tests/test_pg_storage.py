"""Tests for PGStorage — covers connect, close, _get_conn, is_connected,
store, store_facts, recall_important, recall_recent_decisions, count, truncate.

Pattern: real PGStorage with _conn = MagicMock() (no real DB needed).
"""

import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone
from contextlib import contextmanager

from src.storage.postgres import PGStorage


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def pg():
    """PGStorage with mocked direct connection."""
    storage = PGStorage("postgresql://mock:mock@localhost/test")
    storage._conn = MagicMock()
    return storage


def _make_row(
    id: str,
    content: str,
    agent_id: str = "ag-1",
    memory_type: str = "episodic",
    shared: bool = False,
) -> dict:
    return {
        "id": id,
        "agent_id": agent_id,
        "memory_type": memory_type,
        "content": content,
        "source_session": "sess-1",
        "shared": shared,
        "shared_by": agent_id if shared else None,
        "created_at": datetime(2026, 3, 24, tzinfo=timezone.utc),
    }


NOW = datetime(2026, 3, 24, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# connect()  — line 41
# ---------------------------------------------------------------------------

class TestConnect:
    def test_connect_creates_pool(self):
        """connect() instantiates ConnectionPool with the right URL and size limits."""
        from psycopg.rows import dict_row
        storage = PGStorage("postgresql://user:pass@localhost/db")
        with patch("src.storage.postgres.ConnectionPool") as MockPool:
            storage.connect()
            _, kwargs = MockPool.call_args
            assert MockPool.call_args[0][0] == "postgresql://user:pass@localhost/db"
            assert kwargs["min_size"] == 2
            assert kwargs["max_size"] == 10
            assert kwargs["kwargs"]["row_factory"] is dict_row

    def test_connect_assigns_pool(self):
        """connect() sets _pool on the instance."""
        storage = PGStorage("postgresql://user:pass@localhost/db")
        with patch("src.storage.postgres.ConnectionPool") as MockPool:
            storage.connect()
        assert storage._pool is MockPool.return_value


# ---------------------------------------------------------------------------
# close()  — lines 49-54
# ---------------------------------------------------------------------------

class TestClose:
    def test_close_clears_pool(self):
        """close() calls pool.close() and sets _pool to None."""
        storage = PGStorage("postgresql://mock/test")
        mock_pool = MagicMock()
        storage._pool = mock_pool
        storage.close()
        mock_pool.close.assert_called_once()
        assert storage._pool is None

    def test_close_clears_conn(self):
        """close() calls _conn.close() and sets _conn to None."""
        storage = PGStorage("postgresql://mock/test")
        mock_conn = MagicMock()
        storage._conn = mock_conn
        storage.close()
        mock_conn.close.assert_called_once()
        assert storage._conn is None

    def test_close_both_pool_and_conn(self):
        """close() cleans up both pool and conn when both are set."""
        storage = PGStorage("postgresql://mock/test")
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        storage._pool = mock_pool
        storage._conn = mock_conn
        storage.close()
        mock_pool.close.assert_called_once()
        mock_conn.close.assert_called_once()
        assert storage._pool is None
        assert storage._conn is None

    def test_close_no_op_when_nothing_set(self):
        """close() is safe to call when pool and conn are both None."""
        storage = PGStorage("postgresql://mock/test")
        storage.close()  # must not raise


# ---------------------------------------------------------------------------
# _get_conn()  — lines 62-65 (pool path and RuntimeError path)
# ---------------------------------------------------------------------------

class TestGetConn:
    def test_get_conn_uses_pool_when_no_direct_conn(self):
        """_get_conn() yields from pool.connection() when _conn is None."""
        storage = PGStorage("postgresql://mock/test")
        mock_conn = MagicMock()
        mock_pool = MagicMock()
        # pool.connection() is a context manager
        mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)
        storage._pool = mock_pool

        with storage._get_conn() as conn:
            assert conn is mock_conn
        mock_pool.connection.assert_called_once()

    def test_get_conn_raises_when_no_pool_and_no_conn(self):
        """_get_conn() raises RuntimeError when neither _conn nor _pool is set."""
        storage = PGStorage("postgresql://mock/test")
        with pytest.raises(RuntimeError, match="Not connected to PostgreSQL"):
            with storage._get_conn():
                pass  # should never reach here


# ---------------------------------------------------------------------------
# is_connected()  — lines 68-73
# ---------------------------------------------------------------------------

class TestIsConnected:
    def test_is_connected_returns_true_on_success(self, pg):
        """is_connected() returns True when SELECT 1 executes without error."""
        result = pg.is_connected()
        pg._conn.execute.assert_called_once_with("SELECT 1")
        assert result is True

    def test_is_connected_returns_false_on_exception(self, pg):
        """is_connected() returns False when the connection raises."""
        pg._conn.execute.side_effect = Exception("connection refused")
        result = pg.is_connected()
        assert result is False


# ---------------------------------------------------------------------------
# store()  — lines 90-103
# ---------------------------------------------------------------------------

class TestStore:
    def test_store_executes_insert(self, pg):
        """store() runs an INSERT and commits."""
        pg.store(
            memory_id="mem-1",
            text="nginx on port 80",
            agent_id="ag-1",
            session_id="sess-1",
            created_at=NOW,
        )
        assert pg._conn.execute.called
        sql, params = pg._conn.execute.call_args[0]
        assert "INSERT INTO memories" in sql
        assert "mem-1" in params
        assert "nginx on port 80" in params
        assert "ag-1" in params
        pg._conn.commit.assert_called_once()

    def test_store_passes_embedding_as_string(self, pg):
        """store() converts list embedding to string for ::vector cast."""
        embedding = [0.1, 0.2, 0.3]
        pg.store(
            memory_id="mem-2",
            text="test",
            agent_id="ag-1",
            session_id="sess-1",
            created_at=NOW,
            embedding=embedding,
        )
        _, params = pg._conn.execute.call_args[0]
        assert str(embedding) in params

    def test_store_none_embedding_passes_none(self, pg):
        """store() passes None when no embedding provided."""
        pg.store(
            memory_id="mem-3",
            text="test",
            agent_id="ag-1",
            session_id="sess-1",
            created_at=NOW,
            embedding=None,
        )
        _, params = pg._conn.execute.call_args[0]
        # emb_str is the 6th param (index 5)
        assert params[5] is None

    def test_store_shared_sets_shared_by(self, pg):
        """store() sets shared_by=agent_id when shared=True."""
        pg.store(
            memory_id="mem-4",
            text="shared fact",
            agent_id="ag-1",
            session_id="sess-1",
            created_at=NOW,
            shared=True,
        )
        _, params = pg._conn.execute.call_args[0]
        # shared_by is params[8]
        assert params[8] == "ag-1"

    def test_store_not_shared_sets_shared_by_none(self, pg):
        """store() sets shared_by=None when shared=False."""
        pg.store(
            memory_id="mem-5",
            text="private",
            agent_id="ag-1",
            session_id="sess-1",
            created_at=NOW,
            shared=False,
        )
        _, params = pg._conn.execute.call_args[0]
        assert params[8] is None

    def test_store_provenance_serialized_as_json(self, pg):
        """store() converts provenance dict to JSON string."""
        import json
        prov = {"source": "test", "model": "claude"}
        pg.store(
            memory_id="mem-6",
            text="test",
            agent_id="ag-1",
            session_id="sess-1",
            created_at=NOW,
            provenance=prov,
        )
        _, params = pg._conn.execute.call_args[0]
        assert params[6] == json.dumps(prov)


# ---------------------------------------------------------------------------
# store_facts()  — lines 119-139
# ---------------------------------------------------------------------------

def _setup_transaction(pg) -> None:
    """Wire up pg._conn.transaction() as a no-op context manager."""
    pg._conn.transaction.return_value.__enter__ = MagicMock(return_value=None)
    pg._conn.transaction.return_value.__exit__ = MagicMock(return_value=False)


def _store_facts(pg, facts, **kwargs):
    """Call pg.store_facts() with sensible defaults for test params."""
    return pg.store_facts(
        facts=facts,
        agent_id=kwargs.get("agent_id", "ag-1"),
        session_id=kwargs.get("session_id", "sess-1"),
        source_memory_id=kwargs.get("source_memory_id", "src-1"),
        created_at=kwargs.get("created_at", NOW),
        **{k: v for k, v in kwargs.items()
           if k not in ("agent_id", "session_id", "source_memory_id", "created_at")},
    )


class TestStoreFacts:
    def test_store_facts_returns_ids(self, pg):
        """store_facts() returns one UUID string per fact."""
        _setup_transaction(pg)
        ids = _store_facts(pg, ["fact A", "fact B", "fact C"])
        assert len(ids) == 3
        import uuid
        for id_ in ids:
            uuid.UUID(id_)  # raises if invalid

    def test_store_facts_executes_insert_per_fact(self, pg):
        """store_facts() calls execute once per fact inside a transaction."""
        _setup_transaction(pg)
        _store_facts(pg, ["fact X", "fact Y"])
        assert pg._conn.execute.call_count == 2

    def test_store_facts_sql_contains_semantic_type(self, pg):
        """store_facts() inserts rows with memory_type='semantic'."""
        _setup_transaction(pg)
        _store_facts(pg, ["a fact"])
        sql = pg._conn.execute.call_args[0][0]
        assert "'semantic'" in sql

    def test_store_facts_uses_provided_embeddings(self, pg):
        """store_facts() passes embedding[i] as string for each fact."""
        _setup_transaction(pg)
        embs = [[0.1, 0.2], [0.3, 0.4]]
        _store_facts(pg, ["f1", "f2"], embeddings=embs)
        calls = pg._conn.execute.call_args_list
        assert str(embs[0]) in calls[0][0][1]
        assert str(embs[1]) in calls[1][0][1]

    def test_store_facts_empty_list_returns_empty(self, pg):
        """store_facts() with empty facts returns [] without DB calls."""
        _setup_transaction(pg)
        ids = _store_facts(pg, [])
        assert ids == []
        pg._conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# recall_important()  — lines 241-253
# ---------------------------------------------------------------------------

class TestRecallImportant:
    def test_recall_important_returns_results(self, pg):
        """recall_important() returns formatted memory dicts."""
        pg._conn.execute.return_value.fetchall.return_value = [
            _make_row("mem-a", "high priority insight"),
            _make_row("mem-b", "another important fact"),
        ]
        results = pg.recall_important("ag-1", limit=8)
        assert len(results) == 2
        assert results[0]["id"] == "mem-a"
        assert results[1]["content"] == "another important fact"

    def test_recall_important_sql_orders_by_importance(self, pg):
        """recall_important() SQL orders by importance DESC."""
        pg._conn.execute.return_value.fetchall.return_value = []
        pg.recall_important("ag-1")
        sql = pg._conn.execute.call_args[0][0]
        assert "importance DESC" in sql

    def test_recall_important_excludes_zero_importance(self, pg):
        """recall_important() SQL filters out importance=0 rows (chunks)."""
        pg._conn.execute.return_value.fetchall.return_value = []
        pg.recall_important("ag-1")
        sql = pg._conn.execute.call_args[0][0]
        assert "importance > 0" in sql

    def test_recall_important_passes_agent_id_and_limit(self, pg):
        """recall_important() passes agent_id and limit as query params."""
        pg._conn.execute.return_value.fetchall.return_value = []
        pg.recall_important("ag-42", limit=3)
        params = pg._conn.execute.call_args[0][1]
        assert "ag-42" in params
        assert 3 in params

    def test_recall_important_includes_shared(self, pg):
        """recall_important() SQL includes shared=TRUE memories."""
        pg._conn.execute.return_value.fetchall.return_value = []
        pg.recall_important("ag-1")
        sql = pg._conn.execute.call_args[0][0]
        assert "shared = TRUE" in sql

    def test_recall_important_empty_returns_empty_list(self, pg):
        """recall_important() returns [] when no rows match."""
        pg._conn.execute.return_value.fetchall.return_value = []
        assert pg.recall_important("ag-1") == []


# ---------------------------------------------------------------------------
# recall_recent_decisions()  — lines 261-275
# ---------------------------------------------------------------------------

class TestRecallRecentDecisions:
    def test_recall_recent_decisions_returns_results(self, pg):
        """recall_recent_decisions() returns formatted rows."""
        pg._conn.execute.return_value.fetchall.return_value = [
            _make_row("dec-1", "decided to use pgvector because it avoids extra infra", memory_type="semantic"),
        ]
        results = pg.recall_recent_decisions("ag-1", limit=5)
        assert len(results) == 1
        assert results[0]["id"] == "dec-1"

    def test_recall_recent_decisions_sql_filters_semantic_type(self, pg):
        """recall_recent_decisions() SQL restricts to memory_type='semantic'."""
        pg._conn.execute.return_value.fetchall.return_value = []
        pg.recall_recent_decisions("ag-1")
        sql = pg._conn.execute.call_args[0][0]
        assert "memory_type = 'semantic'" in sql

    def test_recall_recent_decisions_sql_uses_ilike_patterns(self, pg):
        """recall_recent_decisions() SQL uses ILIKE for decided/because/chose."""
        pg._conn.execute.return_value.fetchall.return_value = []
        pg.recall_recent_decisions("ag-1")
        sql = pg._conn.execute.call_args[0][0]
        assert "ILIKE" in sql
        assert "decided" in sql.lower()
        assert "because" in sql.lower()
        assert "chose" in sql.lower()

    def test_recall_recent_decisions_orders_by_created_at_desc(self, pg):
        """recall_recent_decisions() SQL orders by created_at DESC."""
        pg._conn.execute.return_value.fetchall.return_value = []
        pg.recall_recent_decisions("ag-1")
        sql = pg._conn.execute.call_args[0][0]
        assert "created_at DESC" in sql

    def test_recall_recent_decisions_passes_agent_id_and_limit(self, pg):
        """recall_recent_decisions() passes correct params."""
        pg._conn.execute.return_value.fetchall.return_value = []
        pg.recall_recent_decisions("ag-77", limit=2)
        params = pg._conn.execute.call_args[0][1]
        assert "ag-77" in params
        assert 2 in params

    def test_recall_recent_decisions_empty_returns_empty_list(self, pg):
        """recall_recent_decisions() returns [] when no rows."""
        pg._conn.execute.return_value.fetchall.return_value = []
        assert pg.recall_recent_decisions("ag-1") == []

    def test_recall_recent_decisions_uses_subtype_filter(self, pg):
        """recall_recent_decisions() SQL prefers structural subtype='decision'.

        Post-2026-04-15 P1 fix: decisions are tagged structurally at write
        time. ILIKE stays as a legacy-row fallback, but the primary filter
        is provenance->>'subtype' = 'decision'.
        """
        pg._conn.execute.return_value.fetchall.return_value = []
        pg.recall_recent_decisions("ag-1")
        sql = pg._conn.execute.call_args[0][0]
        assert "provenance->>'subtype' = 'decision'" in sql


# ---------------------------------------------------------------------------
# store_with_facts_and_chunks()  — transactional aggregate write
# ---------------------------------------------------------------------------

class TestStoreWithFactsAndChunks:
    """2026-04-15 P1 fix: episodic + semantic + chunks in one transaction."""

    def _setup(self, pg):
        """Wire pg._conn.transaction() as a no-op context manager."""
        pg._conn.transaction.return_value.__enter__ = MagicMock(return_value=None)
        pg._conn.transaction.return_value.__exit__ = MagicMock(return_value=False)

    def test_single_transaction_for_all_writes(self, pg):
        """Episodic + facts + decisions + chunks share one transaction."""
        self._setup(pg)
        pg.store_with_facts_and_chunks(
            memory_id="m1",
            text="parent text",
            agent_id="ag-1",
            session_id="sess-1",
            created_at=NOW,
            facts=["f1"],
            decisions=["d1"],
            chunks=["c1", "c2"],
        )
        # One transaction spans everything
        pg._conn.transaction.assert_called_once()
        # Expect: 1 episodic + 2 semantic (fact + decision) + 2 chunks = 5 executes
        assert pg._conn.execute.call_count == 5

    def test_empty_facts_and_chunks(self, pg):
        """Minimal write (episodic only) issues exactly one insert."""
        self._setup(pg)
        pg.store_with_facts_and_chunks(
            memory_id="m1",
            text="just text",
            agent_id="ag-1",
            session_id="sess-1",
            created_at=NOW,
        )
        assert pg._conn.execute.call_count == 1

    def test_subtypes_written_into_provenance(self, pg):
        """Facts get subtype='fact'; decisions get subtype='decision'."""
        import json
        self._setup(pg)
        pg.store_with_facts_and_chunks(
            memory_id="m1",
            text="parent",
            agent_id="ag-1",
            session_id="sess-1",
            created_at=NOW,
            facts=["F"],
            decisions=["D"],
        )
        # 2nd call = fact insert, 3rd call = decision insert
        fact_params = pg._conn.execute.call_args_list[1][0][1]
        dec_params = pg._conn.execute.call_args_list[2][0][1]
        # provenance is the 6th positional param in the INSERT
        fact_prov = json.loads(fact_params[5])
        dec_prov = json.loads(dec_params[5])
        assert fact_prov["subtype"] == "fact"
        assert dec_prov["subtype"] == "decision"

    def test_chunk_rows_carry_parent_reference(self, pg):
        """Chunk rows have provenance.parent_memory_id + chunk=True."""
        import json
        self._setup(pg)
        pg.store_with_facts_and_chunks(
            memory_id="parent-xyz",
            text="parent",
            agent_id="ag-1",
            session_id="sess-1",
            created_at=NOW,
            chunks=["chunk a"],
        )
        # 2nd call = chunk insert (no facts/decisions)
        chunk_params = pg._conn.execute.call_args_list[1][0][1]
        chunk_prov = json.loads(chunk_params[5])
        assert chunk_prov["parent_memory_id"] == "parent-xyz"
        assert chunk_prov["chunk"] is True

    def test_rollback_propagates_chunk_failure(self, pg):
        """If a chunk insert raises, the error propagates so the transaction rolls back."""
        self._setup(pg)
        # 1st (episodic) OK, 2nd (chunk) raises
        pg._conn.execute.side_effect = [MagicMock(), RuntimeError("chunk blew up")]
        with pytest.raises(RuntimeError, match="chunk blew up"):
            pg.store_with_facts_and_chunks(
                memory_id="m1",
                text="parent",
                agent_id="ag-1",
                session_id="sess-1",
                created_at=NOW,
                chunks=["c1"],
            )


# ---------------------------------------------------------------------------
# count()  — lines 278-280
# ---------------------------------------------------------------------------

class TestCount:
    def test_count_returns_integer(self, pg):
        """count() returns the integer from SELECT count(*)."""
        pg._conn.execute.return_value.fetchone.return_value = {"n": 42}
        result = pg.count()
        assert result == 42

    def test_count_zero_when_empty(self, pg):
        """count() returns 0 when table is empty."""
        pg._conn.execute.return_value.fetchone.return_value = {"n": 0}
        assert pg.count() == 0

    def test_count_sql_queries_memories_table(self, pg):
        """count() queries the memories table."""
        pg._conn.execute.return_value.fetchone.return_value = {"n": 5}
        pg.count()
        sql = pg._conn.execute.call_args[0][0]
        assert "memories" in sql
        assert "count" in sql.lower()


# ---------------------------------------------------------------------------
# truncate()  — lines 283-285
# ---------------------------------------------------------------------------

class TestTruncate:
    def test_truncate_executes_truncate_statement(self, pg):
        """truncate() runs TRUNCATE memories and commits."""
        pg.truncate()
        sql = pg._conn.execute.call_args[0][0]
        assert "TRUNCATE" in sql
        assert "memories" in sql
        pg._conn.commit.assert_called_once()

    def test_truncate_commits_after_truncate(self, pg):
        """truncate() always commits."""
        pg.truncate()
        pg._conn.commit.assert_called_once()
