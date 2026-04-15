"""JSONL file storage layer — source of truth (Diderot pattern)."""

import json
import os
from pathlib import Path


class JSONLStorage:
    def __init__(self, nas_path: str):
        self._nas_path = Path(nas_path)

    def is_writable(self) -> bool:
        """True if the NAS path exists and is writable.

        Preferred check — works for real mounts, bind mounts, symlinks,
        and subfolder-dev setups. 2026-04-15 review P2.
        """
        p = self._nas_path
        return p.exists() and p.is_dir() and os.access(p, os.W_OK)

    def is_mounted(self) -> bool:
        """Legacy alias for is_writable(). Kept for callers that still
        check `mount` semantics explicitly; prefer is_writable().
        """
        return self.is_writable()

    def append(self, record: dict, agent_id: str, session_id: str) -> Path:
        """Append a record to the agent's session JSONL file.

        Returns the path to the file written.
        Raises OSError if NAS is not mounted or not writable.
        """
        if not self.is_mounted():
            raise OSError(f"NAS not mounted at {self._nas_path}")

        session_dir = self._nas_path / "agents" / agent_id / "episodic"
        session_dir.mkdir(parents=True, exist_ok=True)

        file_path = session_dir / f"{session_id}.jsonl"

        line = json.dumps(record, default=str, ensure_ascii=False)
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

        return file_path

    def append_shared(self, record: dict, session_id: str) -> Path:
        """Append a promoted record to the shared episodic JSONL.

        Raises OSError if NAS is not mounted or not writable.
        """
        if not self.is_mounted():
            raise OSError(f"NAS not mounted at {self._nas_path}")

        shared_dir = self._nas_path / "shared" / "episodic"
        shared_dir.mkdir(parents=True, exist_ok=True)

        file_path = shared_dir / f"{session_id}.jsonl"

        line = json.dumps(record, default=str, ensure_ascii=False)
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

        return file_path

    def read_all_iter(self):
        """Yield records across all agent JSONL files.

        Unordered — callers that need timestamp ordering should collect via
        read_all(). 2026-04-15 review P2: avoids materialising all records
        in memory during a rebuild.
        """
        agents_dir = self._nas_path / "agents"
        if not agents_dir.exists():
            return

        for agent_dir in sorted(agents_dir.iterdir()):
            episodic_dir = agent_dir / "episodic"
            if not episodic_dir.exists():
                continue
            for jsonl_file in sorted(episodic_dir.glob("*.jsonl")):
                for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        yield json.loads(line)

    def read_all(self) -> list[dict]:
        """Read all JSONL records across all agents, sorted by timestamp."""
        records = list(self.read_all_iter())
        records.sort(key=lambda r: r.get("timestamp", ""))
        return records
