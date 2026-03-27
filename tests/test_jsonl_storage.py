"""Tests for JSONL storage layer (NAS write-ahead).

These use a tmp_path fixture to simulate the NAS mount — no real NAS needed.
"""

import json
import pytest
from unittest.mock import patch

from src.storage.jsonl import JSONLStorage


@pytest.fixture
def nas(tmp_path):
    """JSONLStorage pointed at a tmp directory that acts as a mounted NAS."""
    storage = JSONLStorage(str(tmp_path))
    # Patch is_mounted to return True (tmp_path is not a real mount point)
    with patch.object(storage, "is_mounted", return_value=True):
        yield storage


@pytest.fixture
def record():
    """A minimal valid JSONL record."""
    return {
        "id": "abc-123",
        "agent_id": "ag-1",
        "timestamp": "2026-03-24T12:00:00+00:00",
        "type": "episodic",
        "content": "Redis runs on port 6379",
        "session_id": "sess-1",
        "metadata": {},
    }


class TestAppend:
    """specs/jsonl-storage.md — append scenarios."""

    def test_creates_file_at_correct_path(self, nas, record, tmp_path):
        """Append creates file at {nas_path}/agents/ag-1/episodic/sess-1.jsonl."""
        nas.append(record=record, agent_id="ag-1", session_id="sess-1")
        expected = tmp_path / "agents" / "ag-1" / "episodic" / "sess-1.jsonl"
        assert expected.exists()

    def test_second_append_same_session(self, nas, record, tmp_path):
        """Second append to same session appends (not overwrites)."""
        nas.append(record=record, agent_id="ag-1", session_id="sess-1")

        record2 = {**record, "id": "def-456", "content": "Second memory"}
        nas.append(record=record2, agent_id="ag-1", session_id="sess-1")

        path = tmp_path / "agents" / "ag-1" / "episodic" / "sess-1.jsonl"
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_different_session_different_file(self, nas, record, tmp_path):
        """Different session_id creates a different file."""
        nas.append(record=record, agent_id="ag-1", session_id="sess-1")

        record2 = {**record, "id": "def-456", "session_id": "sess-2"}
        nas.append(record=record2, agent_id="ag-1", session_id="sess-2")

        assert (tmp_path / "agents" / "ag-1" / "episodic" / "sess-1.jsonl").exists()
        assert (tmp_path / "agents" / "ag-1" / "episodic" / "sess-2.jsonl").exists()

    def test_each_line_is_valid_json(self, nas, record, tmp_path):
        """Every appended line parses as valid JSON."""
        nas.append(record=record, agent_id="ag-1", session_id="sess-1")
        record2 = {**record, "id": "def-456"}
        nas.append(record=record2, agent_id="ag-1", session_id="sess-1")

        path = tmp_path / "agents" / "ag-1" / "episodic" / "sess-1.jsonl"
        for line in path.read_text().strip().split("\n"):
            parsed = json.loads(line)
            assert "id" in parsed
            assert "content" in parsed


class TestAppendShared:
    """specs/jsonl-storage.md — append_shared scenarios."""

    def test_shared_writes_to_shared_directory(self, nas, record, tmp_path):
        """Promoted record goes to {nas_path}/shared/episodic/."""
        nas.append_shared(record=record, session_id="sess-1")
        expected = tmp_path / "shared" / "episodic" / "sess-1.jsonl"
        assert expected.exists()


class TestAppendUnmounted:
    """specs/jsonl-storage.md — unmounted NAS scenario."""

    def test_raises_on_unmounted_nas(self, tmp_path):
        """append() raises OSError when NAS is not mounted."""
        storage = JSONLStorage(str(tmp_path))
        # Don't patch is_mounted — tmp_path won't be a real mount point
        with pytest.raises(OSError, match="NAS not mounted"):
            storage.append(record={"id": "x"}, agent_id="ag-1", session_id="s")

    def test_shared_raises_on_unmounted_nas(self, tmp_path):
        """append_shared() raises OSError when NAS is not mounted."""
        storage = JSONLStorage(str(tmp_path))
        with pytest.raises(OSError, match="NAS not mounted"):
            storage.append_shared(record={"id": "x"}, session_id="s")


class TestReadAll:
    """specs/jsonl-storage.md — read_all scenarios."""

    def test_read_all_sorted_by_timestamp(self, nas, tmp_path):
        """read_all returns records sorted by timestamp ascending."""
        r1 = {"id": "a", "timestamp": "2026-03-24T14:00:00", "content": "later"}
        r2 = {"id": "b", "timestamp": "2026-03-24T10:00:00", "content": "earlier"}

        nas.append(record=r1, agent_id="ag-1", session_id="s1")
        nas.append(record=r2, agent_id="ag-1", session_id="s1")

        results = nas.read_all()
        assert len(results) == 2
        assert results[0]["id"] == "b"  # earlier timestamp first
        assert results[1]["id"] == "a"

    def test_read_all_empty_returns_empty(self, tmp_path):
        """read_all on empty NAS returns empty list."""
        storage = JSONLStorage(str(tmp_path))
        assert storage.read_all() == []


class TestIsMounted:
    """specs/jsonl-storage.md — is_mounted scenario."""

    def test_is_mounted_false_for_tmp_path(self, tmp_path):
        """tmp_path is not a real mount point."""
        storage = JSONLStorage(str(tmp_path))
        # tmp_path is just a directory, not a mount point
        assert storage.is_mounted() is False
