"""Tests for OrchestratorDispatcher — concrete StageDispatcher implementation."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auto_dev_loop.dispatcher import OrchestratorDispatcher, build_branch_name
from auto_dev_loop.models import (
    AgentDef, Config, Defaults, Issue, TelegramConfig, WorkflowSelectionConfig,
)
from auto_dev_loop.workflow_loader import StageConfig


def _issue(**kw):
    defaults = dict(id=1, number=42, repo="owner/repo", title="Fix the login bug", body="It crashes")
    defaults.update(kw)
    return Issue(**defaults)


def _config():
    return Config(
        telegram=TelegramConfig(bot_token="t", chat_id=1),
        model_roles={"default": "claude-sonnet-4-5", "smol": "claude-haiku-4-5"},
        repos=[],
        defaults=Defaults(),
        workflow_selection=WorkflowSelectionConfig(),
    )


def _agents():
    return {
        "architect": AgentDef(name="architect", description="", system_prompt="plan", tools=["Bash"], model_role="default"),
        "plan_reviewer": AgentDef(name="plan_reviewer", description="", system_prompt="review", tools=["Read"], model_role="default"),
        "reviewer": AgentDef(name="reviewer", description="", system_prompt="review", tools=["Read"], model_role="default"),
        "orchestrator": AgentDef(name="orchestrator", description="", system_prompt="orch", tools=["Bash"], model_role="default"),
        "pr_fixer": AgentDef(name="pr_fixer", description="", system_prompt="fix", tools=["Bash"], model_role="default"),
    }


def _dispatcher(**kw):
    defaults = dict(
        agents=_agents(),
        config=_config(),
        worktree=Path("/tmp/wt"),
        guard=None,
        telegram=None,
        issue=_issue(),
    )
    defaults.update(kw)
    return OrchestratorDispatcher(**defaults)


# --- build_branch_name (F25) ---

def test_build_branch_name():
    issue = _issue(number=42, title="Fix the login bug")
    assert build_branch_name(issue) == "adl/42-fix-the-login-bug"


def test_build_branch_name_truncates():
    issue = _issue(number=1, title="A very long title that exceeds thirty characters easily")
    name = build_branch_name(issue)
    assert name.startswith("adl/1-")
    # The slug part should be truncated to ~30 chars
    slug = name[len("adl/1-"):]
    assert len(slug) <= 35


# --- dispatch_single ---

@pytest.mark.asyncio
async def test_dispatch_single_calls_agent_query():
    d = _dispatcher()
    stage = StageConfig(ref="plan", agent="architect")
    prior = {}

    with patch("auto_dev_loop.dispatcher.agent_query", new_callable=AsyncMock, return_value="the plan") as mock_aq:
        result = await d.dispatch_single(stage, _issue(), prior)

    assert result == "the plan"
    mock_aq.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_single_includes_prior_outputs_in_prompt():
    d = _dispatcher()
    stage = StageConfig(ref="plan_review", agent="plan_reviewer")
    prior = {"plan": "## The Plan\nDo stuff"}

    with patch("auto_dev_loop.dispatcher.agent_query", new_callable=AsyncMock, return_value="APPROVED") as mock_aq:
        await d.dispatch_single(stage, _issue(), prior)

    # Extract the prompt from the call
    call_args = mock_aq.call_args
    prompt = call_args.kwargs.get("prompt", "")
    assert "The Plan" in prompt


# --- dispatch_infrastructure ---

@pytest.mark.asyncio
async def test_dispatch_infrastructure_create_pr():
    d = _dispatcher()
    stage = StageConfig(ref="create_pr", agent="_infra", type="infrastructure")
    prior = {}

    with patch("auto_dev_loop.dispatcher.create_pr", new_callable=AsyncMock, return_value=99):
        result = await d.dispatch_infrastructure(stage, _issue(), prior)

    assert d.pr_number == 99
    assert "APPROVED" in result


@pytest.mark.asyncio
async def test_dispatch_infrastructure_pr_review():
    d = _dispatcher()
    d.pr_number = 99
    stage = StageConfig(ref="pr_review", agent="_infra", type="infrastructure")
    prior = {}

    mock_result = MagicMock()
    mock_result.cycles = 2
    mock_result.merged = True

    with patch("auto_dev_loop.dispatcher.review_loop", new_callable=AsyncMock, return_value=mock_result):
        result = await d.dispatch_infrastructure(stage, _issue(), prior)

    assert "APPROVED" in result


# --- escalate_to_human ---

@pytest.mark.asyncio
async def test_escalate_to_human_no_telegram():
    """Without Telegram, escalation auto-approves."""
    d = _dispatcher(telegram=None)
    stage = StageConfig(ref="review", agent="reviewer")
    from auto_dev_loop.models import ReviewVerdict
    result = await d.escalate_to_human(
        _issue(), stage, ReviewVerdict(approved=False, feedback="bad"), "iteration_cap",
    )
    assert result == "approve"


@pytest.mark.asyncio
async def test_escalate_to_human_with_telegram():
    mock_tg = AsyncMock()
    mock_tg.escalate.return_value = MagicMock(action="reject")
    d = _dispatcher(telegram=mock_tg)
    stage = StageConfig(ref="review", agent="reviewer")
    from auto_dev_loop.models import ReviewVerdict
    result = await d.escalate_to_human(
        _issue(), stage, ReviewVerdict(approved=False, feedback="bad"), "iteration_cap",
    )
    assert result == "reject"
