"""Tests for configuration from environment variables."""

import os
import pytest
from unittest.mock import patch

from src.config import Config


class TestConfigFromEnv:
    """Config.from_env() scenarios from specs/config.md."""

    def test_missing_pg_url_raises(self):
        """Missing PG_URL raises ValueError immediately."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="PG_URL"):
                Config.from_env()

    def test_defaults_with_only_pg_url(self):
        """Only PG_URL set; everything else uses sensible defaults."""
        env = {"PG_URL": "postgresql://test:test@localhost/test"}
        with patch.dict(os.environ, env, clear=True):
            cfg = Config.from_env()
            assert cfg.pg_url == "postgresql://test:test@localhost/test"
            assert cfg.nas_path == "/mnt/memory"
            assert cfg.server_port == 8888
            assert cfg.server_host == "0.0.0.0"
            assert cfg.anthropic_api_key is None
            assert cfg.ollama_base_url is None

    def test_all_env_vars_read(self):
        """All environment variables are read when set."""
        env = {
            "PG_URL": "postgresql://u:p@host/db",
            "NAS_PATH": "/data/mem",
            "SERVER_PORT": "9999",
            "SERVER_HOST": "127.0.0.1",
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "OLLAMA_BASE_URL": "http://localhost:11434",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = Config.from_env()
            assert cfg.pg_url == "postgresql://u:p@host/db"
            assert cfg.nas_path == "/data/mem"
            assert cfg.server_port == 9999
            assert cfg.server_host == "127.0.0.1"
            assert cfg.anthropic_api_key == "sk-ant-test"
            assert cfg.ollama_base_url == "http://localhost:11434"

    def test_server_port_is_integer(self):
        """SERVER_PORT is parsed as int, not left as string."""
        env = {"PG_URL": "postgresql://x:x@x/x", "SERVER_PORT": "7777"}
        with patch.dict(os.environ, env, clear=True):
            cfg = Config.from_env()
            assert isinstance(cfg.server_port, int)
            assert cfg.server_port == 7777
