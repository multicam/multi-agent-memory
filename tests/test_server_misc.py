"""Tests for memory_status() and main() in server.py.

Covers memory_status() return dict and main() startup sequence.
"""

import pytest
from unittest.mock import MagicMock, patch

from src.config import Config
import src.server as server_mod


@pytest.fixture(autouse=True)
def _use_server_mocks(server_mocks, monkeypatch):
    """Wire up shared server mocks + a mock config for all tests."""
    mock_config = MagicMock(spec=Config)
    mock_config.nas_path = "/mnt/memory"
    mock_config.anthropic_api_key = "sk-test"
    mock_config.ollama_base_url = None
    mock_config.server_host = "0.0.0.0"
    mock_config.server_port = 8888

    monkeypatch.setattr(server_mod, "config", mock_config)
    server_mocks.config = mock_config
    _use_server_mocks.mocks = server_mocks


def _pg():
    return _use_server_mocks.mocks.pg


def _jsonl():
    return _use_server_mocks.mocks.jsonl


def _embedder():
    return _use_server_mocks.mocks.embedder


def _config():
    return _use_server_mocks.mocks.config


@pytest.mark.unit
class TestMemoryStatus:
    """memory_status() return dict."""

    def test_returns_all_expected_keys(self):
        """memory_status returns pg, nas, nas_path, embedding_model, extraction."""
        result = server_mod.memory_status()
        assert set(result.keys()) == {"pg", "nas", "nas_path", "embedding_model", "extraction"}

    def test_pg_connected_when_is_connected_true(self):
        """pg field is 'connected' when pg.is_connected() returns True."""
        _pg().is_connected.return_value = True
        result = server_mod.memory_status()
        assert result["pg"] == "connected"

    def test_pg_disconnected_when_is_connected_false(self):
        """pg field is 'disconnected' when pg.is_connected() returns False."""
        _pg().is_connected.return_value = False
        result = server_mod.memory_status()
        assert result["pg"] == "disconnected"

    def test_nas_mounted_when_is_mounted_true(self):
        """nas field is 'mounted' when jsonl.is_mounted() returns True."""
        _jsonl().is_mounted.return_value = True
        result = server_mod.memory_status()
        assert result["nas"] == "mounted"

    def test_nas_unmounted_when_is_mounted_false(self):
        """nas field is 'unmounted' when jsonl.is_mounted() returns False."""
        _jsonl().is_mounted.return_value = False
        result = server_mod.memory_status()
        assert result["nas"] == "unmounted"

    def test_nas_path_matches_config(self):
        """nas_path echoes the configured NAS path."""
        result = server_mod.memory_status()
        assert result["nas_path"] == "/mnt/memory"

    def test_embedding_model_from_embedder(self):
        """embedding_model comes from embedder.model_name."""
        _embedder().model_name = "sentence-transformers/all-MiniLM-L6-v2"
        result = server_mod.memory_status()
        assert result["embedding_model"] == "sentence-transformers/all-MiniLM-L6-v2"

    def test_extraction_haiku_when_anthropic_key_set(self):
        """extraction is 'haiku' when anthropic_api_key is non-empty."""
        _config().anthropic_api_key = "sk-ant-real-key"
        result = server_mod.memory_status()
        assert result["extraction"] == "haiku"

    def test_extraction_ollama_when_no_anthropic_key_but_ollama_url(self):
        """extraction is 'ollama' when no anthropic key but ollama_base_url is set."""
        _config().anthropic_api_key = None
        _config().ollama_base_url = "http://localhost:11434"
        result = server_mod.memory_status()
        assert result["extraction"] == "ollama"

    def test_extraction_disabled_when_neither_backend_configured(self):
        """extraction is 'disabled' when neither anthropic key nor ollama url is set."""
        _config().anthropic_api_key = None
        _config().ollama_base_url = None
        result = server_mod.memory_status()
        assert result["extraction"] == "disabled"


@pytest.mark.unit
class TestMain:
    """main() startup sequence."""

    def test_calls_pg_connect(self):
        """main() calls pg.connect() to establish the database connection."""
        with patch.object(server_mod.mcp, "run"), \
             patch.object(server_mod, "_check_schema"):
            server_mod.main()
        _pg().connect.assert_called_once()

    def test_calls_embedder_load(self):
        """main() calls embedder.load() to initialise the embedding model."""
        with patch.object(server_mod.mcp, "run"), \
             patch.object(server_mod, "_check_schema"):
            server_mod.main()
        _embedder().load.assert_called_once()

    def test_pg_connect_before_embedder_load(self):
        """pg.connect() is called before embedder.load()."""
        call_order = []
        _pg().connect.side_effect = lambda: call_order.append("pg.connect")
        _embedder().load.side_effect = lambda: call_order.append("embedder.load")

        with patch.object(server_mod.mcp, "run"), \
             patch.object(server_mod, "_check_schema"):
            server_mod.main()

        assert call_order.index("pg.connect") < call_order.index("embedder.load")

    def test_calls_mcp_run_with_streamable_http(self):
        """main() starts the MCP server via mcp.run(transport='streamable-http', ...)."""
        with patch.object(server_mod.mcp, "run") as mock_run, \
             patch.object(server_mod, "_check_schema"):
            server_mod.main()

        mock_run.assert_called_once_with(
            transport="streamable-http",
            host=_config().server_host,
            port=_config().server_port,
        )

    def test_prints_startup_messages(self, capsys):
        """main() prints at least four informational lines before starting the server."""
        with patch.object(server_mod.mcp, "run"), \
             patch.object(server_mod, "_check_schema"):
            server_mod.main()

        captured = capsys.readouterr()
        lines = [ln for ln in captured.out.splitlines() if ln.strip()]
        assert len(lines) >= 4

    def test_print_includes_nas_path(self, capsys):
        """Startup output includes the configured NAS path."""
        with patch.object(server_mod.mcp, "run"), \
             patch.object(server_mod, "_check_schema"):
            server_mod.main()

        captured = capsys.readouterr()
        assert "/mnt/memory" in captured.out

    def test_print_includes_embedding_model(self, capsys):
        """Startup output includes the embedding model name."""
        _embedder().model_name = "test-model"
        with patch.object(server_mod.mcp, "run"), \
             patch.object(server_mod, "_check_schema"):
            server_mod.main()

        captured = capsys.readouterr()
        assert "test-model" in captured.out
