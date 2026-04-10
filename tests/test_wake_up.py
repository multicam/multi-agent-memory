"""Tests for the wake_up MCP tool (specs/wake-up.md).

Tests the layered recall protocol for session start.
"""

import os
import pytest
from unittest.mock import MagicMock

os.environ.setdefault("PG_URL", "postgresql://mock:mock@localhost/test")

import src.server as server_mod


@pytest.fixture(autouse=True)
def _patch_server_globals(monkeypatch):
    """Replace server module globals with mocks."""
    mock_pg = MagicMock()
    mock_pg.is_connected.return_value = True

    mock_jsonl = MagicMock()
    mock_jsonl.is_mounted.return_value = True

    mock_embedder = MagicMock()
    mock_embedder.model_name = "test-model"

    mock_extractor = MagicMock()

    monkeypatch.setattr(server_mod, "pg", mock_pg)
    monkeypatch.setattr(server_mod, "jsonl", mock_jsonl)
    monkeypatch.setattr(server_mod, "embedder", mock_embedder)
    monkeypatch.setattr(server_mod, "extractor", mock_extractor)

    _patch_server_globals.pg = mock_pg


def _pg():
    return _patch_server_globals.pg


@pytest.mark.integration
class TestWakeUpLayerStructure:
    """specs/wake-up.md -- layer structure."""

    def test_returns_all_layers(self):
        """wake_up returns layer_1_critical, layer_2_decisions, and token_estimate."""
        _pg().recall_important.return_value = [
            {"id": "1", "content": "Redis runs on port 6379", "agent_id": "ag-1"},
        ]
        _pg().recall_recent_decisions.return_value = [
            {"id": "2", "content": "Decided to use Redis because fast", "agent_id": "ag-1"},
        ]

        result = server_mod.wake_up("ag-1")

        assert "layer_1_critical" in result
        assert "layer_2_decisions" in result
        assert "token_estimate" in result

    def test_layer_1_from_recall_important(self):
        """layer_1_critical comes from pg.recall_important()."""
        memories = [
            {"id": "1", "content": "critical fact", "agent_id": "ag-1"},
            {"id": "2", "content": "another critical", "agent_id": "ag-1"},
        ]
        _pg().recall_important.return_value = memories
        _pg().recall_recent_decisions.return_value = []

        result = server_mod.wake_up("ag-1")

        assert len(result["layer_1_critical"]) == 2
        _pg().recall_important.assert_called_once_with("ag-1", limit=8)

    def test_layer_2_from_recall_decisions(self):
        """layer_2_decisions comes from pg.recall_recent_decisions()."""
        decisions = [
            {"id": "3", "content": "Decided X because Y", "agent_id": "ag-1"},
        ]
        _pg().recall_important.return_value = []
        _pg().recall_recent_decisions.return_value = decisions

        result = server_mod.wake_up("ag-1")

        assert len(result["layer_2_decisions"]) == 1
        _pg().recall_recent_decisions.assert_called_once_with("ag-1", limit=5)


@pytest.mark.integration
class TestWakeUpTokenEstimate:
    """specs/wake-up.md -- token estimate."""

    def test_token_estimate_calculation(self):
        """token_estimate = sum of len(content) // 4 for all memories."""
        _pg().recall_important.return_value = [
            {"id": "1", "content": "a" * 100, "agent_id": "ag-1"},  # 25 tokens
        ]
        _pg().recall_recent_decisions.return_value = [
            {"id": "2", "content": "b" * 200, "agent_id": "ag-1"},  # 50 tokens
        ]

        result = server_mod.wake_up("ag-1")

        assert result["token_estimate"] == 75  # 25 + 50

    def test_empty_result_zero_tokens(self):
        """Empty results give 0 token estimate."""
        _pg().recall_important.return_value = []
        _pg().recall_recent_decisions.return_value = []

        result = server_mod.wake_up("ag-1")

        assert result["token_estimate"] == 0


@pytest.mark.integration
class TestWakeUpErrorHandling:
    """specs/wake-up.md -- error handling."""

    def test_empty_agent_id_rejected(self):
        """Empty agent_id returns error dict."""
        result = server_mod.wake_up("")
        assert "error" in result

    def test_pg_failure_returns_empty_layers(self):
        """PG failure returns empty layers, not a crash."""
        _pg().recall_important.side_effect = Exception("PG down")
        _pg().recall_recent_decisions.side_effect = Exception("PG down")

        result = server_mod.wake_up("ag-1")

        assert result["layer_1_critical"] == []
        assert result["layer_2_decisions"] == []
        assert result["token_estimate"] == 0

    def test_partial_pg_failure(self):
        """One layer failing doesn't prevent the other from returning."""
        _pg().recall_important.return_value = [
            {"id": "1", "content": "important stuff", "agent_id": "ag-1"},
        ]
        _pg().recall_recent_decisions.side_effect = Exception("PG down")

        result = server_mod.wake_up("ag-1")

        assert len(result["layer_1_critical"]) == 1
        assert result["layer_2_decisions"] == []
