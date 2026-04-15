"""Tests for the last coverage gaps (post-cruise 94.53% → 100%).

Each test is anchored to a specific file:line that the coverage report flagged.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import src.server as server_mod
from src.embeddings import Embedder
from src.storage.postgres import PGStorage


NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def _conn_cm(conn):
    """Wrap a connection object as a no-op context manager for _get_conn/get_conn patches."""

    @contextmanager
    def _cm():
        yield conn

    return _cm


# ---------------------------------------------------------------------------
# src/embeddings.py:42, 44  — embed_batch edge cases
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestEmbedBatchEdges:
    def test_empty_input_returns_empty_list_without_model_call(self):
        with patch("src.embeddings.SentenceTransformer") as mock_cls:
            embedder = Embedder()
            assert embedder.embed_batch([]) == []
            mock_cls.assert_not_called()

    def test_unloaded_model_raises_runtime_error(self):
        embedder = Embedder()
        with pytest.raises(RuntimeError, match="Embedding model not loaded"):
            embedder.embed_batch(["some text"])


# ---------------------------------------------------------------------------
# src/storage/postgres.py:73-74  — public get_conn wrapper
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestGetConnWrapper:
    def test_get_conn_yields_the_underlying_connection(self):
        storage = PGStorage("postgresql://mock:mock@localhost/test")
        fake_conn = MagicMock(name="pool-conn")
        with patch.object(storage, "_get_conn", new=_conn_cm(fake_conn)):
            with storage.get_conn() as conn:
                assert conn is fake_conn


# ---------------------------------------------------------------------------
# src/storage/postgres.py:242  — chunk embedding str() conversion
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestChunkEmbeddingAssignment:
    def test_chunk_embedding_stringified_when_present(self, mock_pg):
        """A non-None chunk_embeddings[i] flows to the chunk INSERT as str()."""
        chunk_insert_params = []

        fake_conn = MagicMock()
        fake_conn.transaction.return_value.__enter__ = lambda s: None
        fake_conn.transaction.return_value.__exit__ = lambda s, *a: None

        def execute_side_effect(sql, params=None):
            if params and len(params) >= 6:
                emb = params[4]
                # Chunk inserts have importance=0.0 (params[-2]) — distinct from the main row
                if isinstance(params[-2], float) and params[-2] == 0.0:
                    chunk_insert_params.append(params)
            return MagicMock()

        fake_conn.execute.side_effect = execute_side_effect

        with patch.object(mock_pg, "_get_conn", new=_conn_cm(fake_conn)):
            mock_pg.store_with_facts_and_chunks(
                memory_id="mem-id",
                text="x" * 2000,
                agent_id="ag-1",
                session_id="sess-1",
                created_at=NOW,
                embedding=[0.1] * 768,
                provenance={},
                chunks=["chunk-a", "chunk-b"],
                chunk_embeddings=[[0.2] * 768, None],
            )

        assert len(chunk_insert_params) == 2, "expected two chunk INSERTs"
        first_emb = chunk_insert_params[0][4]
        second_emb = chunk_insert_params[1][4]
        assert isinstance(first_emb, str) and first_emb.startswith("[0.2"), first_emb
        assert second_emb is None


# ---------------------------------------------------------------------------
# src/server.py:74-83  — _init_state lazy construction
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestInitStateLazyPaths:
    def test_constructs_each_global_when_all_are_none(self, monkeypatch):
        """_init_state materialises config, pg, jsonl, embedder, extractor when all are None."""
        monkeypatch.setattr(server_mod, "config", None)
        monkeypatch.setattr(server_mod, "pg", None)
        monkeypatch.setattr(server_mod, "jsonl", None)
        monkeypatch.setattr(server_mod, "embedder", None)
        monkeypatch.setattr(server_mod, "extractor", None)

        fake_config = MagicMock()
        fake_config.pg_url = "postgresql://x"
        fake_config.nas_path = "/tmp/nas"
        fake_config.anthropic_api_key = None
        fake_config.ollama_base_url = None

        with patch("src.server.Config") as MockConfig, \
             patch("src.server.PGStorage") as MockPG, \
             patch("src.server.JSONLStorage") as MockJSONL, \
             patch("src.server.Embedder") as MockEmbedder, \
             patch("src.server.FactExtractor") as MockExtractor:
            MockConfig.from_env.return_value = fake_config

            server_mod._init_state(need_jsonl=True, need_extractor=True)

            MockConfig.from_env.assert_called_once_with()
            MockPG.assert_called_once_with("postgresql://x")
            MockJSONL.assert_called_once_with("/tmp/nas")
            MockEmbedder.assert_called_once()
            MockExtractor.assert_called_once()


# ---------------------------------------------------------------------------
# src/server.py:177-178  — embed_batch failure is non-fatal
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestStoreMemoryEmbedBatchFailure:
    def test_embed_batch_failure_logs_and_continues(self, server_mocks, caplog):
        server_mocks.embedder.embed_batch.side_effect = RuntimeError("GPU oom")
        server_mocks.pg.check_duplicate.return_value = None
        server_mocks.pg.store_with_facts_and_chunks.return_value = None

        import logging

        with caplog.at_level(logging.WARNING, logger="agent-memory"):
            result = server_mod.store_memory("hello world", "ag-1", "sess-1")

        # Storage path still ran — not a both-failed error.
        assert not (isinstance(result, dict) and "Both" in str(result.get("error", "")))
        assert any("Semantic embedding failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# src/server.py:352-365  — _check_schema gate
# ---------------------------------------------------------------------------

def _pg_with_applied(applied_filenames):
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchall.return_value = [
        {"filename": f} for f in applied_filenames
    ]
    fake_pg = MagicMock()
    fake_pg.get_conn = _conn_cm(fake_conn)
    return fake_pg


def _point_server_at(tmp_path, monkeypatch):
    """Make Path(__file__).parent.parent resolve to tmp_path (so migrations/ is there)."""
    monkeypatch.setattr(server_mod, "__file__", str(tmp_path / "src" / "server.py"))


@pytest.mark.unit
class TestCheckSchema:
    def test_passes_when_all_migrations_applied(self, tmp_path, monkeypatch):
        (tmp_path / "migrations").mkdir()
        (tmp_path / "migrations" / "001_initial.sql").write_text("-- test")
        (tmp_path / "migrations" / "002_add_bm25.sql").write_text("-- test")
        _point_server_at(tmp_path, monkeypatch)

        server_mod._check_schema(_pg_with_applied(["001_initial.sql", "002_add_bm25.sql"]))

    def test_raises_when_a_migration_is_missing(self, tmp_path, monkeypatch):
        (tmp_path / "migrations").mkdir()
        (tmp_path / "migrations" / "001_initial.sql").write_text("-- test")
        (tmp_path / "migrations" / "999_new_feature.sql").write_text("-- test")
        _point_server_at(tmp_path, monkeypatch)

        with pytest.raises(RuntimeError, match="999_new_feature.sql"):
            server_mod._check_schema(_pg_with_applied(["001_initial.sql"]))

    def test_no_migrations_dir_is_noop(self, tmp_path, monkeypatch):
        _point_server_at(tmp_path, monkeypatch)
        server_mod._check_schema(MagicMock())

    def test_empty_migrations_dir_is_noop(self, tmp_path, monkeypatch):
        (tmp_path / "migrations").mkdir()
        _point_server_at(tmp_path, monkeypatch)
        server_mod._check_schema(MagicMock())
