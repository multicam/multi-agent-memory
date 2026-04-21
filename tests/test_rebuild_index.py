"""Tests for rebuild_index.py — verifies atomic write pattern."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

NOW = datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.unit
class TestRebuildAtomicWrites:
    """rebuild_index.py must use store_with_facts_and_chunks for atomic writes."""

    def test_uses_atomic_store(self):
        """Rebuild calls store_with_facts_and_chunks, not separate store+store_facts."""
        import scripts.rebuild_index as mod

        mock_config = MagicMock()
        mock_config.pg_url = "postgresql://x"
        mock_config.nas_path = "/tmp/nas"

        mock_jsonl = MagicMock()
        mock_jsonl.is_mounted.return_value = True
        mock_jsonl.read_all.return_value = [
            {
                "id": "mem-1",
                "agent_id": "ag-1",
                "timestamp": NOW.isoformat(),
                "content": "test content",
                "session_id": "sess-1",
                "type": "episodic",
                "extraction": {
                    "facts": ["fact a"],
                    "decisions": ["decided X"],
                    "model": "test",
                    "status": "success",
                    "extracted_at": NOW.isoformat(),
                },
                "promoted": False,
            }
        ]

        mock_pg = MagicMock()
        mock_pg.count.return_value = 0

        with patch.object(mod, "Config") as MockConfig, \
             patch.object(mod, "JSONLStorage", return_value=mock_jsonl), \
             patch.object(mod, "PGStorage", return_value=mock_pg), \
             patch.object(mod, "Embedder") as MockEmbedder, \
             patch("sys.argv", ["rebuild_index.py", "--no-embeddings"]):
            MockConfig.from_env.return_value = mock_config
            MockEmbedder.return_value.embed.return_value = [0.1] * 768

            mod.main()

        # Must call the atomic aggregate, not the separate methods
        mock_pg.store_with_facts_and_chunks.assert_called_once()
        mock_pg.store.assert_not_called()
        mock_pg.store_facts.assert_not_called()

    def test_atomic_call_receives_facts_and_decisions(self):
        """store_with_facts_and_chunks receives facts and decisions separately."""
        import scripts.rebuild_index as mod

        mock_config = MagicMock()
        mock_config.pg_url = "postgresql://x"
        mock_config.nas_path = "/tmp/nas"

        mock_jsonl = MagicMock()
        mock_jsonl.is_mounted.return_value = True
        mock_jsonl.read_all.return_value = [
            {
                "id": "mem-2",
                "agent_id": "ag-1",
                "timestamp": NOW.isoformat(),
                "content": "another memory",
                "session_id": "sess-2",
                "type": "episodic",
                "extraction": {
                    "facts": ["f1", "f2"],
                    "decisions": ["d1"],
                    "model": "test",
                    "status": "success",
                    "extracted_at": NOW.isoformat(),
                },
                "promoted": True,
            }
        ]

        mock_pg = MagicMock()
        mock_pg.count.return_value = 0

        with patch.object(mod, "Config") as MockConfig, \
             patch.object(mod, "JSONLStorage", return_value=mock_jsonl), \
             patch.object(mod, "PGStorage", return_value=mock_pg), \
             patch.object(mod, "Embedder") as MockEmbedder, \
             patch("sys.argv", ["rebuild_index.py", "--no-embeddings"]):
            MockConfig.from_env.return_value = mock_config
            MockEmbedder.return_value.embed.return_value = [0.1] * 768

            mod.main()

        kw = mock_pg.store_with_facts_and_chunks.call_args.kwargs
        assert kw["facts"] == ["f1", "f2"]
        assert kw["decisions"] == ["d1"]
        assert kw["shared"] is True
