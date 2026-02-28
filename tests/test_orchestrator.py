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
async def test_process_issue_uses_workflow_engine():
    """process_issue should load workflow, create dispatcher, and call execute_workflow."""
    from auto_dev_loop.models import WorkflowResult

    with patch("auto_dev_loop.orchestrator.create_worktree"):
        with patch("auto_dev_loop.orchestrator.delete_worktree"):
            with patch("auto_dev_loop.orchestrator.select_workflow", return_value="bug_fix"):
                with patch("auto_dev_loop.orchestrator.load_all_workflows") as mock_load_wf:
                    mock_wf = MagicMock()
                    mock_load_wf.return_value = {"bug_fix": mock_wf}
                    with patch("auto_dev_loop.orchestrator.load_agents", return_value={}):
                        with patch("auto_dev_loop.orchestrator.execute_workflow") as mock_exec:
                            mock_exec.return_value = WorkflowResult(status="completed")
                            with patch("auto_dev_loop.orchestrator.OrchestratorDispatcher") as mock_disp_cls:
                                mock_disp = MagicMock()
                                mock_disp.pr_number = 99
                                mock_disp_cls.return_value = mock_disp
                                result = await process_issue(
                                    _issue(), _config(), repo_path=Path("/tmp/repo"),
                                )

    assert result.state == IssueState.COMPLETED
    assert result.pr_number == 99
    mock_exec.assert_called_once()


@pytest.mark.asyncio
async def test_process_issue_escalated():
    from auto_dev_loop.models import WorkflowResult

    with patch("auto_dev_loop.orchestrator.create_worktree"):
        with patch("auto_dev_loop.orchestrator.delete_worktree"):
            with patch("auto_dev_loop.orchestrator.select_workflow", return_value="feature"):
                with patch("auto_dev_loop.orchestrator.load_all_workflows") as mock_load_wf:
                    mock_load_wf.return_value = {"feature": MagicMock()}
                    with patch("auto_dev_loop.orchestrator.load_agents", return_value={}):
                        with patch("auto_dev_loop.orchestrator.execute_workflow") as mock_exec:
                            mock_exec.return_value = WorkflowResult(status="escalated", stage="review")
                            with patch("auto_dev_loop.orchestrator.OrchestratorDispatcher") as mock_disp_cls:
                                mock_disp_cls.return_value = MagicMock(pr_number=None)
                                result = await process_issue(
                                    _issue(), _config(), repo_path=Path("/tmp/repo"),
                                )

    assert result.state == IssueState.ESCALATED


@pytest.mark.asyncio
async def test_process_issue_exception_maps_to_failed():
    """Any exception during workflow execution maps to FAILED."""
    with patch("auto_dev_loop.orchestrator.create_worktree"):
        with patch("auto_dev_loop.orchestrator.delete_worktree"):
            with patch("auto_dev_loop.orchestrator.select_workflow", return_value="feature"):
                with patch("auto_dev_loop.orchestrator.load_all_workflows") as mock_load_wf:
                    mock_load_wf.return_value = {"feature": MagicMock()}
                    with patch("auto_dev_loop.orchestrator.load_agents", return_value={}):
                        with patch("auto_dev_loop.orchestrator.execute_workflow", side_effect=RuntimeError("boom")):
                            with patch("auto_dev_loop.orchestrator.OrchestratorDispatcher"):
                                result = await process_issue(
                                    _issue(), _config(), repo_path=Path("/tmp/repo"),
                                )

    assert result.state == IssueState.FAILED
    assert "boom" in result.error


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

    with patch("auto_dev_loop.pr.asyncio.create_subprocess_exec", return_value=push_proc):
        with pytest.raises(RuntimeError, match="git push failed"):
            await create_pr(_issue(), Path("/tmp/wt"))


@pytest.mark.asyncio
async def test_create_pr_success():
    push_proc = _mock_proc(0)
    pr_proc = _mock_proc(0, stdout="https://github.com/owner/repo/pull/99")

    procs = [push_proc, pr_proc]
    with patch("auto_dev_loop.pr.asyncio.create_subprocess_exec", side_effect=procs):
        pr_number = await create_pr(_issue(), Path("/tmp/wt"))
    assert pr_number == 99
