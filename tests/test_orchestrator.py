"""Tests for the orchestrator — issue lifecycle state machine."""

from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from auto_dev_loop.orchestrator import (
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
