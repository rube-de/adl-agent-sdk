"""Tests for the Agent Teams dev loop."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auto_dev_loop.dev_loop import (
    dev_loop,
    MaxDevCyclesError,
    run_agent_team,
    TeamResult,
)
from auto_dev_loop.models import VERDICT_TESTS_PASSING, Issue, AgentDef, Config, TelegramConfig, Defaults, ReviewVerdict


def _issue():
    return Issue(id=1, number=42, repo="owner/repo", title="Fix bug", body="Crashes")


def _config(max_cycles=5):
    return Config(
        telegram=TelegramConfig(bot_token="t", chat_id=1),
        model_roles={"default": "claude-sonnet-4-5", "smol": "claude-haiku-4-5"},
        repos=[],
        defaults=Defaults(
            max_dev_cycles=max_cycles,
            external_reviewers=["gemini"],
            external_review_timeout=300,
        ),
    )


def _agents():
    return {
        "orchestrator": AgentDef(
            name="orchestrator", description="", system_prompt="orch",
            tools=["Bash"], model_role="default", max_turns=50,
        ),
        "reviewer": AgentDef(
            name="reviewer", description="", system_prompt="review",
            tools=["Read"], model_role="default", max_turns=30,
        ),
    }


@pytest.mark.asyncio
async def test_dev_loop_approved_first_cycle():
    team_result = TeamResult(tests_passing=True, diff="diff content")
    review_result = MagicMock()
    review_result.verdict = ReviewVerdict(approved=True, feedback=None)

    with patch("auto_dev_loop.dev_loop.load_agents", return_value=_agents()):
        with patch("auto_dev_loop.dev_loop.run_agent_team", return_value=team_result):
            with patch("auto_dev_loop.dev_loop.multi_model_review", return_value=review_result):
                result = await dev_loop(_issue(), "the plan", Path("/tmp/wt"), _config())

    assert result.diff == "diff content"
    assert result.cycles == 1


@pytest.mark.asyncio
async def test_dev_loop_retry_after_rejection():
    team_result = TeamResult(tests_passing=True, diff="diff v2")

    call_count = {"review": 0}

    async def mock_review(**kw):
        call_count["review"] += 1
        if call_count["review"] == 1:
            result = MagicMock()
            result.verdict = ReviewVerdict(approved=False, feedback="Fix imports")
            return result
        result = MagicMock()
        result.verdict = ReviewVerdict(approved=True, feedback=None)
        return result

    with patch("auto_dev_loop.dev_loop.load_agents", return_value=_agents()):
        with patch("auto_dev_loop.dev_loop.run_agent_team", return_value=team_result):
            with patch("auto_dev_loop.dev_loop.multi_model_review", side_effect=mock_review):
                result = await dev_loop(_issue(), "plan", Path("/tmp/wt"), _config())

    assert result.cycles == 2


@pytest.mark.asyncio
async def test_dev_loop_max_cycles_exceeded():
    team_result = TeamResult(tests_passing=True, diff="diff")
    review_result = MagicMock()
    review_result.verdict = ReviewVerdict(approved=False, feedback="Still bad")

    with patch("auto_dev_loop.dev_loop.load_agents", return_value=_agents()):
        with patch("auto_dev_loop.dev_loop.run_agent_team", return_value=team_result):
            with patch("auto_dev_loop.dev_loop.multi_model_review", return_value=review_result):
                with pytest.raises(MaxDevCyclesError):
                    await dev_loop(_issue(), "plan", Path("/tmp/wt"), _config(max_cycles=2))


@pytest.mark.asyncio
async def test_dev_loop_tests_fail_no_review():
    """If tests don't pass, skip review and retry."""
    call_count = {"team": 0}

    async def mock_team(**kw):
        call_count["team"] += 1
        if call_count["team"] == 1:
            return TeamResult(tests_passing=False, diff="")
        return TeamResult(tests_passing=True, diff="fixed diff")

    review_result = MagicMock()
    review_result.verdict = ReviewVerdict(approved=True, feedback=None)

    with patch("auto_dev_loop.dev_loop.load_agents", return_value=_agents()):
        with patch("auto_dev_loop.dev_loop.run_agent_team", side_effect=mock_team):
            with patch("auto_dev_loop.dev_loop.multi_model_review", return_value=review_result):
                result = await dev_loop(_issue(), "plan", Path("/tmp/wt"), _config())

    assert result.cycles == 2
    assert result.diff == "fixed diff"


@pytest.mark.asyncio
async def test_substring_match_false_positive():
    """Ensure TESTS_PASSING marker embedded in other text doesn't match."""
    # Simulate agent output that mentions the marker in prose but didn't emit it on its own line
    agent_output = f"Checked for {VERDICT_TESTS_PASSING} but tests actually failed."

    with patch("auto_dev_loop.dev_loop.agent_query", return_value=agent_output):
        with patch("auto_dev_loop.dev_loop.asyncio.create_subprocess_exec") as mock_proc:
            mock_proc.return_value.communicate = AsyncMock(return_value=(b"diff", None))
            result = await run_agent_team(
                issue=_issue(), plan="plan", agents=_agents(),
                worktree=Path("/tmp/wt"), config=_config(), cycle=1,
            )

    # Should NOT detect tests as passing — marker was not on its own line
    assert result.tests_passing is False
