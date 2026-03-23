"""JSONL file storage layer — source of truth (Diderot pattern)."""

import json
import os
from pathlib import Path


class JSONLStorage:
    def __init__(self, nas_path: str):
        self._nas_path = Path(nas_path)

    def is_mounted(self) -> bool:
        return os.path.ismount(self._nas_path)

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

    def read_all(self) -> list[dict]:
        """Read all JSONL records across all agents, sorted by timestamp."""
        records = []
        agents_dir = self._nas_path / "agents"

        if not agents_dir.exists():
            return records

        for agent_dir in sorted(agents_dir.iterdir()):
            episodic_dir = agent_dir / "episodic"
            if not episodic_dir.exists():
                continue
            for jsonl_file in sorted(episodic_dir.glob("*.jsonl")):
                for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))

        records.sort(key=lambda r: r.get("timestamp", ""))
        return records
