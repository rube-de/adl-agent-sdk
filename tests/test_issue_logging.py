"""Tests for per-issue JSONL logging."""

import json
from pathlib import Path

import pytest

from auto_dev_loop.issue_logging import IssueLogger


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


def test_logger_creates_directory(log_dir: Path):
    logger = IssueLogger(log_dir, 42)
    logger.log_event("test", {"key": "value"})
    assert (log_dir / "42").is_dir()


def test_log_event_writes_jsonl(log_dir: Path):
    logger = IssueLogger(log_dir, 42)
    logger.log_event("tool_call", {"tool": "Bash", "command": "ls"})
    logger.log_event("result", {"output": "file.py"})

    log_file = log_dir / "42" / "log.jsonl"
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 2
    event = json.loads(lines[0])
    assert event["type"] == "tool_call"
    assert event["data"]["tool"] == "Bash"


def test_update_context(log_dir: Path):
    logger = IssueLogger(log_dir, 42)
    logger.update_context({"stage": "plan", "iteration": 1})
    logger.update_context({"stage": "dev", "iteration": 2})

    ctx_file = log_dir / "42" / "context.jsonl"
    lines = ctx_file.read_text().strip().splitlines()
    assert len(lines) == 2
    last = json.loads(lines[-1])
    assert last["stage"] == "dev"


def test_write_state(log_dir: Path):
    logger = IssueLogger(log_dir, 42)
    logger.write_state({"state": "PLANNING", "worktree": "/tmp/wt"})

    state_file = log_dir / "42" / "state.json"
    state = json.loads(state_file.read_text())
    assert state["state"] == "PLANNING"


def test_read_state(log_dir: Path):
    logger = IssueLogger(log_dir, 42)
    logger.write_state({"state": "DEV_CYCLE_1"})
    state = logger.read_state()
    assert state["state"] == "DEV_CYCLE_1"


def test_read_state_missing_returns_none(log_dir: Path):
    logger = IssueLogger(log_dir, 42)
    assert logger.read_state() is None
