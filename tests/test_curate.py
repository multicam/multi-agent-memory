"""Tests for curate.py — verifies connection cleanup and basic logic."""

import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.unit
class TestCurateConnectionCleanup:
    """curate.py must close PG pool even on exceptions."""

    def test_pg_closed_on_llm_exception(self):
        """pg.close() is called even when the Anthropic API raises."""
        import scripts.curate as mod

        mock_config = MagicMock()
        mock_config.pg_url = "postgresql://x"
        mock_config.anthropic_api_key = "sk-test"
        mock_config.nas_path = "/tmp/nas"

        mock_pg = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            {"id": "abc", "agent_id": "ag-1", "content": "test", "created_at": "2026-01-01"}
        ]
        mock_pg.get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_pg.get_conn.return_value.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")

        with patch.object(mod, "Config") as MockConfig, \
             patch.object(mod, "PGStorage", return_value=mock_pg), \
             patch.object(mod.anthropic, "Anthropic", return_value=mock_client), \
             patch("sys.argv", ["curate.py"]):
            MockConfig.from_env.return_value = mock_config

            # The RuntimeError propagates (not caught), but pg.close must
            # still have been called via the try/finally in main().
            with pytest.raises(RuntimeError, match="API down"):
                mod.main()

        mock_pg.close.assert_called_once()

    def test_pg_closed_on_success(self):
        """pg.close() is called after normal execution."""
        import scripts.curate as mod

        mock_config = MagicMock()
        mock_config.pg_url = "postgresql://x"
        mock_config.anthropic_api_key = "sk-test"
        mock_config.nas_path = "/tmp/nas"

        mock_pg = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_pg.get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_pg.get_conn.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(mod, "Config") as MockConfig, \
             patch.object(mod, "PGStorage", return_value=mock_pg), \
             patch("sys.argv", ["curate.py"]):
            MockConfig.from_env.return_value = mock_config

            mod.main()

        mock_pg.close.assert_called_once()
