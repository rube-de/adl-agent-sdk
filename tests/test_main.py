"""Tests for the main daemon loop."""

import asyncio
import os
import signal
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from auto_dev_loop.main import (
    DaemonState,
    daemon_loop,
    drain_tasks,
    run_poll_cycle,
    should_process_issue,
    issue_key,
    _on_issue_done,
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


def test_should_process_issue_skips_completed():
    state = DaemonState()
    state.completed_keys.add("owner/repo#42")
    issue = Issue(id=0, number=42, repo="owner/repo", title="t", body="b")
    assert should_process_issue(issue, state) is False


@pytest.mark.asyncio
async def test_run_poll_cycle_spawns_task():
    """Normal mode spawns a background task (not inline await)."""
    issues = [Issue(id=0, number=42, repo="owner/repo", title="t", body="b")]
    state = DaemonState()

    mock_process = AsyncMock(return_value=MagicMock(state="completed"))

    with patch("auto_dev_loop.main.poll_project_issues", return_value=issues):
        with patch("auto_dev_loop.main.process_issue", mock_process):
            await run_poll_cycle(_config(), state)
            # Task was spawned — drain to let it complete
            assert "owner/repo#42" in state.active_issues
            assert "owner/repo#42" in state.tasks
            await drain_tasks(state)

    # Callback cleans up after task completes
    assert "owner/repo#42" not in state.active_issues
    assert "owner/repo#42" not in state.tasks
    mock_process.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_poll_cycle_concurrent_tasks():
    """Multiple issues spawn concurrent tasks up to max_concurrent."""
    issues = [
        Issue(id=1, number=10, repo="owner/repo", title="a", body="b"),
        Issue(id=2, number=20, repo="owner/repo", title="c", body="d"),
        Issue(id=3, number=30, repo="owner/repo", title="e", body="f"),
    ]
    state = DaemonState()
    cfg = _config()
    cfg.defaults.max_concurrent = 2

    mock_process = AsyncMock(return_value=MagicMock(state="completed"))

    with patch("auto_dev_loop.main.poll_project_issues", return_value=issues):
        with patch("auto_dev_loop.main.process_issue", mock_process):
            await run_poll_cycle(cfg, state)
            # Only 2 tasks should be spawned (max_concurrent=2)
            assert len(state.tasks) == 2
            await drain_tasks(state)

    assert mock_process.await_count == 2


@pytest.mark.asyncio
async def test_run_poll_cycle_no_issues():
    state = DaemonState()

    with patch("auto_dev_loop.main.poll_project_issues", return_value=[]):
        await run_poll_cycle(_config(), state)

    assert len(state.active_issues) == 0
    assert len(state.tasks) == 0


@pytest.mark.asyncio
async def test_run_poll_cycle_once_processes_single_issue():
    """--once mode processes at most one issue inline then returns."""
    issues = [
        Issue(id=1, number=10, repo="owner/repo", title="a", body="b"),
        Issue(id=2, number=20, repo="owner/repo", title="c", body="d"),
    ]
    state = DaemonState()
    cfg = _config()
    cfg.defaults.max_concurrent = 5  # would normally allow more

    mock_process = AsyncMock(return_value=MagicMock(state="completed"))

    with patch("auto_dev_loop.main.poll_project_issues", return_value=issues):
        with patch("auto_dev_loop.main.process_issue", mock_process):
            await run_poll_cycle(cfg, state, once=True)

    # Only one issue processed, no tasks spawned (inline in --once)
    mock_process.assert_awaited_once()
    assert len(state.tasks) == 0


def test_on_issue_done_callback():
    """Callback removes issue from active set and task dict."""
    state = DaemonState()
    state.active_issues.add("owner/repo#42")
    mock_task = MagicMock()
    state.tasks["owner/repo#42"] = mock_task

    _on_issue_done("owner/repo#42", state, mock_task)

    assert "owner/repo#42" not in state.active_issues
    assert "owner/repo#42" not in state.tasks


@pytest.mark.asyncio
async def test_task_exception_does_not_crash_drain():
    """A failing task should not prevent drain_tasks from completing."""
    state = DaemonState()

    async def _explode():
        raise RuntimeError("boom")

    task = asyncio.get_running_loop().create_task(_explode())
    state.tasks["bad"] = task

    # drain_tasks uses return_exceptions=True, so this should not raise
    await drain_tasks(state)


@pytest.mark.asyncio
async def test_daemon_loop_once_exits():
    """daemon_loop with once=True runs one cycle and returns."""
    mock_cycle = AsyncMock()

    with patch("auto_dev_loop.main.run_poll_cycle", mock_cycle):
        await daemon_loop(_config(), once=True)

    mock_cycle.assert_awaited_once()


def _make_state_store_mock() -> MagicMock:
    mock_store = MagicMock()
    mock_store.init = AsyncMock()
    mock_store.close = AsyncMock()
    mock_store.list_terminal_issue_keys = AsyncMock(return_value=set())
    return mock_store


async def test_daemon_loop_handles_sigterm_gracefully():
    """SIGTERM should cause daemon_loop to exit cleanly, not hang.

    The test overrides the default SIGTERM disposition (terminate) so the
    signal doesn't kill the pytest process.  With no signal handler installed
    by daemon_loop the loop keeps running until wait_for times out — that's
    the expected failure until graceful shutdown is implemented.
    """
    cfg = _config()
    cfg.defaults.poll_interval = 0.01

    mock_store = _make_state_store_mock()
    mock_cycle = AsyncMock()

    # Track whether the cycle ran at least once
    cycles: list[int] = []

    async def _send_sigterm_after_first_cycle(*args, **kwargs):
        cycles.append(1)
        if len(cycles) == 1:
            os.kill(os.getpid(), signal.SIGTERM)

    mock_cycle.side_effect = _send_sigterm_after_first_cycle

    # Override default SIGTERM disposition so the test process isn't killed.
    old_handler = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        with patch("auto_dev_loop.main.StateStore", return_value=mock_store):
            with patch("auto_dev_loop.main.run_poll_cycle", mock_cycle):
                with patch("auto_dev_loop.main.ADL_HOME") as mock_home:
                    mock_home.mkdir = MagicMock()
                    # Should complete quickly once signal handling is implemented;
                    # currently times out because daemon_loop never sees the signal.
                    await asyncio.wait_for(daemon_loop(cfg), timeout=2.0)
    finally:
        signal.signal(signal.SIGTERM, old_handler)

    assert len(cycles) >= 1


async def test_daemon_loop_signal_stops_sleep():
    """After a signal, the inter-poll sleep should be interrupted immediately.

    poll_interval is set to 60 s so that if the sleep is NOT interrupted the
    test will time out rather than pass.  Once signal handling is implemented
    the loop should exit after exactly one cycle.
    """
    cfg = _config()
    cfg.defaults.poll_interval = 60  # long enough that hanging = test failure

    mock_store = _make_state_store_mock()
    cycle_count = 0

    async def _count_and_signal(*args, **kwargs):
        nonlocal cycle_count
        cycle_count += 1
        if cycle_count == 1:
            os.kill(os.getpid(), signal.SIGTERM)

    old_handler = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        with patch("auto_dev_loop.main.StateStore", return_value=mock_store):
            with patch("auto_dev_loop.main.run_poll_cycle", side_effect=_count_and_signal):
                with patch("auto_dev_loop.main.ADL_HOME") as mock_home:
                    mock_home.mkdir = MagicMock()
                    await asyncio.wait_for(daemon_loop(cfg), timeout=2.0)
    finally:
        signal.signal(signal.SIGTERM, old_handler)

    # Exactly one cycle ran before the signal stopped the 60-second sleep
    assert cycle_count == 1
