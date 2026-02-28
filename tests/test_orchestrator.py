"""Tests for the orchestrator — issue lifecycle state machine."""

from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from auto_dev_loop.orchestrator import (
    create_pr,
    process_issue,
    IssueState,
    ProcessResult,
    build_pr_command,
)
from auto_dev_loop.models import Issue, Config, TelegramConfig, Defaults, WorkflowSelectionConfig


def _issue():
    return Issue(id=1, number=42, repo="owner/repo", title="Fix bug", body="Crashes", labels=["bug"])


def _config():
    return Config(
        telegram=TelegramConfig(bot_token="t", chat_id=1),
        model_roles={"default": "claude-sonnet-4-5"},
        repos=[],
        defaults=Defaults(),
        workflow_selection=WorkflowSelectionConfig(
            default="feature",
            label_map={"bug": "bug_fix"},
        ),
    )


@pytest.mark.asyncio
async def test_process_issue_full_lifecycle():
    """Happy path: claim -> workflow -> plan -> dev -> PR -> review -> done."""
    mock_plan_result = MagicMock()
    mock_plan_result.plan = "the plan"
    mock_plan_result.iterations = 1

    mock_dev_result = MagicMock()
    mock_dev_result.diff = "diff"
    mock_dev_result.cycles = 1

    mock_review_result = MagicMock()
    mock_review_result.cycles = 0
    mock_review_result.merged = False

    with patch("auto_dev_loop.orchestrator.create_worktree"):
        with patch("auto_dev_loop.orchestrator.select_workflow", return_value="bug_fix"):
            with patch("auto_dev_loop.orchestrator.plan_loop", return_value=mock_plan_result):
                with patch("auto_dev_loop.orchestrator.dev_loop", return_value=mock_dev_result):
                    with patch("auto_dev_loop.orchestrator.create_pr", return_value=1):
                        with patch("auto_dev_loop.orchestrator.review_loop", return_value=mock_review_result):
                            with patch("auto_dev_loop.orchestrator.delete_worktree"):
                                result = await process_issue(_issue(), _config(), repo_path=Path("/tmp/repo"))

    assert result.state == IssueState.COMPLETED
    assert result.pr_number == 1


@pytest.mark.asyncio
async def test_process_issue_plan_fails():
    from auto_dev_loop.plan_loop import MaxPlanIterationsError

    with patch("auto_dev_loop.orchestrator.create_worktree"):
        with patch("auto_dev_loop.orchestrator.select_workflow", return_value="bug_fix"):
            with patch("auto_dev_loop.orchestrator.plan_loop", side_effect=MaxPlanIterationsError("max")):
                with patch("auto_dev_loop.orchestrator.delete_worktree"):
                    result = await process_issue(_issue(), _config(), repo_path=Path("/tmp/repo"))

    assert result.state == IssueState.FAILED
    assert "max" in result.error


def test_build_pr_command():
    cmd = build_pr_command("owner/repo", "Fix bug", "Implements fix for #42", "feature/42-fix-bug")
    assert cmd[0] == "gh"
    assert "pr" in cmd
    assert "create" in cmd
    assert "--repo" in cmd
    assert "owner/repo" in cmd


# ── create_pr return-code tests ─────────────────────────────────────


def _mock_proc(returncode: int, stdout: str = "", stderr: str = ""):
    proc = AsyncMock()
    proc.communicate.return_value = (stdout.encode(), stderr.encode())
    proc.returncode = returncode
    return proc


@pytest.mark.asyncio
async def test_create_pr_push_fails():
    """git push failure should raise before gh pr create runs."""
    push_proc = _mock_proc(1, stderr="rejected: non-fast-forward")

    with patch("auto_dev_loop.orchestrator.asyncio.create_subprocess_exec", return_value=push_proc):
        with pytest.raises(RuntimeError, match="git push failed"):
            await create_pr(_issue(), Path("/tmp/wt"))


@pytest.mark.asyncio
async def test_create_pr_success():
    push_proc = _mock_proc(0)
    pr_proc = _mock_proc(0, stdout="https://github.com/owner/repo/pull/99")

    procs = [push_proc, pr_proc]
    with patch("auto_dev_loop.orchestrator.asyncio.create_subprocess_exec", side_effect=procs):
        pr_number = await create_pr(_issue(), Path("/tmp/wt"))
    assert pr_number == 99
