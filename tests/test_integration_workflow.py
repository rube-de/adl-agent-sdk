"""Integration test — workflow engine + OrchestratorDispatcher with mocked agent calls."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auto_dev_loop.dispatcher import OrchestratorDispatcher
from auto_dev_loop.models import (
    AgentDef, Config, Defaults, Issue,
    TelegramConfig, WorkflowSelectionConfig,
)
from auto_dev_loop.workflow_engine import execute_workflow
from auto_dev_loop.workflow_loader import StageConfig, WorkflowConfig


def _issue():
    return Issue(id=1, number=42, repo="owner/repo", title="Fix bug", body="Crashes", labels=["bug"])


def _config():
    return Config(
        telegram=TelegramConfig(bot_token="t", chat_id=1),
        model_roles={"default": "claude-sonnet-4-5", "smol": "claude-haiku-4-5"},
        repos=[],
        defaults=Defaults(),
        workflow_selection=WorkflowSelectionConfig(),
    )


def _agents():
    names = ["architect", "plan_reviewer", "orchestrator", "tester", "developer", "reviewer", "pr_fixer"]
    return {
        n: AgentDef(name=n, description="", system_prompt=n, tools=["Bash"], model_role="default")
        for n in names
    }


def _mini_workflow():
    """Minimal workflow: plan -> plan_review (with loopTarget) -> create_pr -> pr_review."""
    return WorkflowConfig(
        id="test_mini",
        description="test",
        stages=[
            StageConfig(ref="plan", agent="architect"),
            StageConfig(ref="plan_review", agent="plan_reviewer", loopTarget="plan", maxIterations=3),
            StageConfig(ref="create_pr", agent="_infra", type="infrastructure"),
            StageConfig(ref="pr_review", agent="_infra", type="infrastructure"),
        ],
    )


@pytest.mark.asyncio
async def test_mini_workflow_happy_path():
    """Plan approved first try -> create_pr -> pr_review -> completed."""
    call_log = []

    async def mock_agent_query(agent_def, prompt, worktree, config, **kw):
        call_log.append(agent_def.name)
        if agent_def.name == "architect":
            return "## Plan\nDo the thing\n\nAPPROVED"
        if agent_def.name == "plan_reviewer":
            return "APPROVED"
        return "APPROVED"

    mock_review_result = MagicMock()
    mock_review_result.cycles = 0
    mock_review_result.merged = True

    with patch("auto_dev_loop.dispatcher.agent_query", side_effect=mock_agent_query):
        with patch("auto_dev_loop.dispatcher.create_pr", new_callable=AsyncMock, return_value=1):
            with patch("auto_dev_loop.dispatcher.review_loop", new_callable=AsyncMock, return_value=mock_review_result):
                dispatcher = OrchestratorDispatcher(
                    agents=_agents(), config=_config(), worktree=Path("/tmp/wt"),
                    guard=None, telegram=None, issue=_issue(),
                )
                result = await execute_workflow(_mini_workflow(), _issue(), dispatcher)

    assert result.status == "completed"
    assert dispatcher.pr_number == 1
    assert call_log == ["architect", "plan_reviewer"]


@pytest.mark.asyncio
async def test_mini_workflow_with_loop_target():
    """Plan rejected once -> loopTarget jumps back to plan -> approved second time."""
    call_log = []

    async def mock_agent_query(agent_def, prompt, worktree, config, **kw):
        call_log.append(agent_def.name)
        if agent_def.name == "architect":
            return "## Plan\nDo the thing\n\nAPPROVED"
        if agent_def.name == "plan_reviewer":
            if call_log.count("plan_reviewer") == 1:
                return "## Feedback\nAdd tests\n\nNEEDS_REVISION"
            return "APPROVED"
        return "APPROVED"

    mock_review_result = MagicMock(cycles=0, merged=True)

    with patch("auto_dev_loop.dispatcher.agent_query", side_effect=mock_agent_query):
        with patch("auto_dev_loop.dispatcher.create_pr", new_callable=AsyncMock, return_value=1):
            with patch("auto_dev_loop.dispatcher.review_loop", new_callable=AsyncMock, return_value=mock_review_result):
                dispatcher = OrchestratorDispatcher(
                    agents=_agents(), config=_config(), worktree=Path("/tmp/wt"),
                    guard=None, telegram=None, issue=_issue(),
                )
                result = await execute_workflow(_mini_workflow(), _issue(), dispatcher)

    assert result.status == "completed"
    # plan -> plan_review (reject) -> plan (re-run) -> plan_review (approve) -> create_pr -> pr_review
    assert call_log == ["architect", "plan_reviewer", "architect", "plan_reviewer"]
