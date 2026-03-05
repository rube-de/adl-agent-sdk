"""Tests for the sequential plan loop."""

from pathlib import Path
from unittest.mock import patch

import pytest

from auto_dev_loop.plan_loop import plan_loop, MaxPlanIterationsError, build_architect_prompt
from auto_dev_loop.models import Issue, AgentDef, Config, TelegramConfig, Defaults, VERDICT_APPROVED, VERDICT_NEEDS_REVISION


def _issue():
    return Issue(id=1, number=42, repo="owner/repo", title="Fix bug", body="Login crashes")


def _config(max_iter=3):
    return Config(
        telegram=TelegramConfig(bot_token="t", chat_id=1),
        model_roles={"default": "claude-sonnet-4-5"},
        repos=[],
        defaults=Defaults(max_plan_iterations=max_iter),
    )


def _agents():
    return {
        "architect": AgentDef(
            name="architect", description="", system_prompt="plan",
            tools=["Bash"], model_role="default", max_turns=50,
        ),
        "plan_reviewer": AgentDef(
            name="plan_reviewer", description="", system_prompt="review",
            tools=["Read"], model_role="default", max_turns=30,
        ),
    }


def test_build_architect_prompt_initial():
    prompt = build_architect_prompt(_issue(), plan=None, feedback=None)
    assert "Fix bug" in prompt
    assert "Login crashes" in prompt


def test_build_architect_prompt_fences_issue_body():
    """Issue body should be wrapped in untrusted content markers."""
    prompt = build_architect_prompt(_issue(), plan=None, feedback=None)
    assert "<untrusted" in prompt
    assert "</untrusted>" in prompt


def test_build_architect_prompt_with_feedback():
    prompt = build_architect_prompt(_issue(), plan="old plan", feedback="needs tests")
    assert "needs tests" in prompt
    assert "old plan" in prompt


@pytest.mark.asyncio
async def test_plan_approved_first_try():
    async def mock_query(agent_def, prompt, worktree, config, **kw):
        if agent_def.name == "architect":
            return "## Plan\nDo the thing"
        return VERDICT_APPROVED

    with patch("auto_dev_loop.plan_loop.agent_query", side_effect=mock_query):
        with patch("auto_dev_loop.plan_loop.load_agents", return_value=_agents()):
            result = await plan_loop(_issue(), Path("/tmp/wt"), _config())

    assert result.plan == "## Plan\nDo the thing"
    assert result.iterations == 1


@pytest.mark.asyncio
async def test_plan_approved_after_revision():
    call_count = {"architect": 0, "plan_reviewer": 0}

    async def mock_query(agent_def, prompt, worktree, config, **kw):
        call_count[agent_def.name] = call_count.get(agent_def.name, 0) + 1
        if agent_def.name == "architect":
            return f"Plan v{call_count['architect']}"
        if call_count["plan_reviewer"] == 1:
            return f"## Feedback\nAdd error handling\n\n{VERDICT_NEEDS_REVISION}"
        return VERDICT_APPROVED

    with patch("auto_dev_loop.plan_loop.agent_query", side_effect=mock_query):
        with patch("auto_dev_loop.plan_loop.load_agents", return_value=_agents()):
            result = await plan_loop(_issue(), Path("/tmp/wt"), _config())

    assert result.iterations == 2


@pytest.mark.asyncio
async def test_plan_max_iterations_exceeded():
    async def mock_query(agent_def, prompt, worktree, config, **kw):
        if agent_def.name == "architect":
            return "bad plan"
        return f"## Feedback\nStill wrong\n\n{VERDICT_NEEDS_REVISION}"

    with patch("auto_dev_loop.plan_loop.agent_query", side_effect=mock_query):
        with patch("auto_dev_loop.plan_loop.load_agents", return_value=_agents()):
            with pytest.raises(MaxPlanIterationsError):
                await plan_loop(_issue(), Path("/tmp/wt"), _config(max_iter=2))
