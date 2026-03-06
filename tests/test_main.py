"""Tests for the main daemon loop."""

import asyncio
import logging
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auto_dev_loop.main import (
    DaemonState,
    _check_legacy_state,
    _get_or_create_store,
    _get_repo_name,
    _on_issue_done,
    daemon_loop,
    drain_tasks,
    issue_key,
    run_poll_cycle,
    should_process_issue,
)
from auto_dev_loop.models import Config, Defaults, Issue, RepoConfig, TelegramConfig


def _config():
    return Config(
        telegram=TelegramConfig(bot_token="t", chat_id=1),
        model_roles={"default": "claude-sonnet-4-5"},
        repos=[
            RepoConfig(path="/tmp/repo", project_number=1),
        ],
        defaults=Defaults(max_concurrent=1),
    )


def _mock_store():
    """Create a mock StateStore for per-repo store tests."""
    store = AsyncMock()
    store.list_terminal_issue_keys.return_value = set()
    return store


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
    mock_store = _mock_store()

    with patch(
        "auto_dev_loop.main._get_or_create_store",
        new=AsyncMock(return_value=mock_store),
    ):
        with patch("auto_dev_loop.main.poll_project_issues", return_value=issues):
            with patch(
                "auto_dev_loop.main._make_issue_logger", return_value=MagicMock()
            ):
                with patch("auto_dev_loop.main.process_issue", mock_process):
                    await run_poll_cycle(_config(), state)
                    # Task was spawned -- drain to let it complete
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
    mock_store = _mock_store()

    with patch(
        "auto_dev_loop.main._get_or_create_store",
        new=AsyncMock(return_value=mock_store),
    ):
        with patch("auto_dev_loop.main.poll_project_issues", return_value=issues):
            with patch(
                "auto_dev_loop.main._make_issue_logger", return_value=MagicMock()
            ):
                with patch("auto_dev_loop.main.process_issue", mock_process):
                    await run_poll_cycle(cfg, state)
                    # Only 2 tasks should be spawned (max_concurrent=2)
                    assert len(state.tasks) == 2
                    await drain_tasks(state)

    assert mock_process.await_count == 2


@pytest.mark.asyncio
async def test_run_poll_cycle_no_issues():
    state = DaemonState()
    mock_store = _mock_store()

    with patch(
        "auto_dev_loop.main._get_or_create_store",
        new=AsyncMock(return_value=mock_store),
    ):
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
    mock_store = _mock_store()

    with patch(
        "auto_dev_loop.main._get_or_create_store",
        new=AsyncMock(return_value=mock_store),
    ):
        with patch("auto_dev_loop.main.poll_project_issues", return_value=issues):
            with patch(
                "auto_dev_loop.main._make_issue_logger", return_value=MagicMock()
            ):
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


@pytest.mark.asyncio
async def test_daemon_loop_handles_sigterm_gracefully():
    """SIGTERM handler triggers graceful shutdown after first cycle."""
    config = _config()
    config.defaults.poll_interval = 0.01

    registered_handlers: dict = {}
    real_loop = asyncio.get_running_loop()

    def capture_handler(sig, callback):
        registered_handlers[sig] = callback

    cycle_count = 0

    async def counted_cycle(cfg, state, *, once=False):
        nonlocal cycle_count
        cycle_count += 1
        # Trigger the SIGTERM handler after the first cycle (if registered)
        if cycle_count == 1 and signal.SIGTERM in registered_handlers:
            registered_handlers[signal.SIGTERM]()

    with patch("auto_dev_loop.main.ADL_HOME"):
        with patch("auto_dev_loop.main.run_poll_cycle", side_effect=counted_cycle):
            with patch.object(
                real_loop, "add_signal_handler", side_effect=capture_handler
            ):
                await asyncio.wait_for(daemon_loop(config), timeout=2.0)

    assert cycle_count == 1


@pytest.mark.asyncio
async def test_daemon_loop_signal_stops_sleep():
    """SIGTERM interrupts the inter-poll sleep so the loop exits quickly."""
    config = _config()
    config.defaults.poll_interval = 60  # Long sleep -- signal must interrupt it

    registered_handlers: dict = {}
    real_loop = asyncio.get_running_loop()

    def capture_handler(sig, callback):
        registered_handlers[sig] = callback

    cycle_count = 0

    async def counted_cycle(cfg, state, *, once=False):
        nonlocal cycle_count
        cycle_count += 1
        if cycle_count == 1 and signal.SIGTERM in registered_handlers:
            # Trigger shutdown while we're still "in" the cycle
            registered_handlers[signal.SIGTERM]()

    with patch("auto_dev_loop.main.ADL_HOME"):
        with patch("auto_dev_loop.main.run_poll_cycle", side_effect=counted_cycle):
            with patch.object(
                real_loop, "add_signal_handler", side_effect=capture_handler
            ):
                await asyncio.wait_for(daemon_loop(config), timeout=2.0)

    # Loop should exit after 1 cycle without sleeping the full 60 seconds
    assert cycle_count == 1


@pytest.mark.asyncio
async def test_run_poll_cycle_respects_shutdown_event():
    """No new tasks are spawned when shutdown_event is already set."""
    issues = [
        Issue(id=1, number=10, repo="owner/repo", title="a", body="b"),
        Issue(id=2, number=20, repo="owner/repo", title="c", body="d"),
    ]
    state = DaemonState()
    cfg = _config()
    cfg.defaults.max_concurrent = 5

    shutdown_event = asyncio.Event()
    shutdown_event.set()  # Already shutting down

    mock_process = AsyncMock(return_value=MagicMock(state="completed"))

    with patch("auto_dev_loop.main.poll_project_issues", return_value=issues):
        with patch("auto_dev_loop.main.process_issue", mock_process):
            await run_poll_cycle(cfg, state, shutdown_event=shutdown_event)

    assert len(state.tasks) == 0
    assert len(state.active_issues) == 0
    mock_process.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_poll_cycle_stops_mid_cycle_on_shutdown():
    """If shutdown_event is set during iteration, no further tasks spawn."""
    issues = [
        Issue(id=1, number=10, repo="owner/repo", title="a", body="b"),
        Issue(id=2, number=20, repo="owner/repo", title="c", body="d"),
        Issue(id=3, number=30, repo="owner/repo", title="e", body="f"),
    ]
    state = DaemonState()
    cfg = _config()
    cfg.defaults.max_concurrent = 5

    shutdown_event = asyncio.Event()
    call_count = 0
    mock_store = _mock_store()

    async def process_and_shutdown(issue, config, **kw):
        nonlocal call_count
        call_count += 1
        # Set shutdown after first task is spawned (task runs inline here via mock)
        shutdown_event.set()
        return MagicMock(state="completed")

    with patch(
        "auto_dev_loop.main._get_or_create_store",
        new=AsyncMock(return_value=mock_store),
    ):
        with patch("auto_dev_loop.main.poll_project_issues", return_value=issues):
            with patch(
                "auto_dev_loop.main._make_issue_logger", return_value=MagicMock()
            ):
                with patch(
                    "auto_dev_loop.main.process_issue", side_effect=process_and_shutdown
                ):
                    await run_poll_cycle(cfg, state, shutdown_event=shutdown_event)
                    await drain_tasks(state)

    # Only 1 task should have been spawned before shutdown took effect
    assert call_count == 1


def test_get_repo_name_from_slash_path():
    cfg = RepoConfig(path="owner/my-repo", project_number=1)
    assert _get_repo_name(cfg) == "my-repo"


def test_get_repo_name_no_slash():
    cfg = RepoConfig(path="my-repo", project_number=1)
    assert _get_repo_name(cfg) == "my-repo"


def test_get_repo_name_absolute_path():
    cfg = RepoConfig(path="/home/user/repos/my-repo", project_number=1)
    assert _get_repo_name(cfg) == "my-repo"


# --- Legacy state migration warning tests (Task 6) ---


def test_legacy_state_warns_when_db_exists_without_repos(tmp_path, caplog):
    legacy_db = tmp_path / "state.db"
    legacy_db.touch()
    repos_dir = tmp_path / "repos"

    with caplog.at_level(logging.WARNING):
        _check_legacy_state(legacy_db, repos_dir)

    assert "migrate" in caplog.text.lower()


def test_no_legacy_warning_when_repos_dir_exists(tmp_path, caplog):
    legacy_db = tmp_path / "state.db"
    legacy_db.touch()
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()

    with caplog.at_level(logging.WARNING):
        _check_legacy_state(legacy_db, repos_dir)

    assert "migrate" not in caplog.text.lower()


def test_no_legacy_warning_when_no_db(tmp_path, caplog):
    legacy_db = tmp_path / "state.db"
    repos_dir = tmp_path / "repos"

    with caplog.at_level(logging.WARNING):
        _check_legacy_state(legacy_db, repos_dir)

    assert "migrate" not in caplog.text.lower()


# --- Integration tests for _get_or_create_store (Task 7) ---


@pytest.mark.asyncio
async def test_get_or_create_store_creates_dir(tmp_path):
    state = DaemonState()
    slug = "owner-my-repo"

    with patch(
        "auto_dev_loop.main.repo_state_dir", return_value=tmp_path / "repos" / slug
    ):
        store = await _get_or_create_store(state, slug)

    try:
        assert (tmp_path / "repos" / slug).is_dir()
        assert (tmp_path / "repos" / slug / "state.db").exists()
        assert slug in state.stores
        tables = await store.list_tables()
        assert "issues" in tables
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_get_or_create_store_reuses_existing(tmp_path):
    state = DaemonState()
    slug = "owner-repo"

    with patch(
        "auto_dev_loop.main.repo_state_dir", return_value=tmp_path / "repos" / slug
    ):
        store1 = await _get_or_create_store(state, slug)
        store2 = await _get_or_create_store(state, slug)

    try:
        assert store1 is store2
    finally:
        await store1.close()
