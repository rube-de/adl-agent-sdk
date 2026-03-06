"""Per-issue JSONL logging with log + context separation."""

from __future__ import annotations

import json
import time
from pathlib import Path


class IssueLogger:
    """Per-issue logging. Three files: log.jsonl, context.jsonl, state.json."""

    def __init__(self, logs_dir: Path, issue_number: int):
        self._dir = logs_dir / str(issue_number)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._dir / "log.jsonl"
        self._context_path = self._dir / "context.jsonl"
        self._state_path = self._dir / "state.json"

    def log_event(self, event_type: str, data: dict) -> None:
        """Append to full log (log.jsonl). Every message, tool call, result."""
        entry = {
            "type": event_type,
            "timestamp": time.time(),
            "data": data,
        }
        with self._log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def update_context(self, context: dict) -> None:
        """Append to active context (context.jsonl). Rebuilt each cycle."""
        entry = {**context, "timestamp": time.time()}
        with self._context_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def write_state(self, state: dict) -> None:
        """Overwrite state.json with current state machine position."""
        self._state_path.write_text(json.dumps(state, indent=2))

    def read_state(self) -> dict | None:
        """Read current state, or None if no state file."""
        if not self._state_path.exists():
            return None
        return json.loads(self._state_path.read_text())

    def clear_context(self) -> None:
        """Clear context.jsonl for a new cycle."""
        if self._context_path.exists():
            self._context_path.unlink()
