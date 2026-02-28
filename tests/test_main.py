"""Tests for the main daemon loop."""

from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from auto_dev_loop.main import (
    DaemonState,
    run_poll_cycle,
    should_process_issue,
    issue_key,
)
from auto_dev_loop.models import Issue, Config, TelegramConfig, Defaults, RepoConfig


def _config():
    return Config(
        telegram=TelegramConfig(bot_token="t", chat_id=1),
        model_roles={"default": "claude-sonnet-4-5"},
        repos=[
            RepoConfig(path="/tmp/repo", project_number=1),
        ],
        defaults=Defaults(max_concurrent=1),
    )


def test_issue_key():
    issue = Issue(id=0, number=42, repo="owner/repo", title="t", body="b")
    assert issue_key(issue) == "owner/repo#42"


def test_should_process_issue_new():
    state = DaemonState()
    issue = Issue(id=0, number=42, repo="owner/repo", title="t", body="b")
    assert should_process_issue(issue, state) is True


def test_should_process_issue_already_active():
    state = DaemonState()
    issue = Issue(id=0, number=42, repo="owner/repo", title="t", body="b")
    state.active_issues.add("owner/repo#42")
    assert should_process_issue(issue, state) is False


def test_should_process_issue_max_concurrent():
    state = DaemonState()
    state.active_issues.add("owner/repo#1")
    issue = Issue(id=0, number=42, repo="owner/repo", title="t", body="b")
    assert should_process_issue(issue, state, max_concurrent=1) is False


@pytest.mark.asyncio
async def test_run_poll_cycle_finds_issues():
    issues = [Issue(id=0, number=42, repo="owner/repo", title="t", body="b")]
    state = DaemonState()

    mock_process = AsyncMock()
    mock_process.return_value = MagicMock(state="completed")

    with patch("auto_dev_loop.main.poll_project_issues", return_value=issues):
        with patch("auto_dev_loop.main.process_issue", mock_process):
            await run_poll_cycle(_config(), state)

    # Issue should be removed from active after processing
    assert "owner/repo#42" not in state.active_issues
    mock_process.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_poll_cycle_no_issues():
    state = DaemonState()

    with patch("auto_dev_loop.main.poll_project_issues", return_value=[]):
        await run_poll_cycle(_config(), state)

    assert len(state.active_issues) == 0
