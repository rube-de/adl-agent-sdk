"""Tests for parallel multi-model review."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from auto_dev_loop.multi_model import (
    multi_model_review,
    build_review_prompt,
    AllReviewersFailedError,
)
from auto_dev_loop.models import AgentDef, Config, TelegramConfig, Defaults, VERDICT_APPROVED, VERDICT_NEEDS_REVISION


def _config():
    return Config(
        telegram=TelegramConfig(bot_token="t", chat_id=1),
        model_roles={"default": "claude-sonnet-4-5"},
        repos=[],
        defaults=Defaults(external_reviewers=["gemini"], external_review_timeout=300),
    )


def _agents():
    return {
        "reviewer": AgentDef(
            name="reviewer", description="", system_prompt="review",
            tools=["Read"], model_role="default", max_turns=30,
        ),
    }


def test_build_review_prompt():
    prompt = build_review_prompt("the plan", "diff --git a/b")
    assert "the plan" in prompt
    assert "diff --git" in prompt


def test_build_review_prompt_uses_verdict_constants():
    """Review prompt should reference the distinctive verdict markers, not bare strings."""
    prompt = build_review_prompt("plan", "diff")
    assert VERDICT_APPROVED in prompt
    assert VERDICT_NEEDS_REVISION in prompt
    # Bare "End with APPROVED" should not appear
    assert "End with APPROVED or" not in prompt


@pytest.mark.asyncio
async def test_all_approved():
    async def mock_query(agent_def, prompt, worktree, config, **kw):
        return f"Looks good.\n\n{VERDICT_APPROVED}"

    async def mock_external(cmd, prompt, worktree, timeout):
        return f"Fine.\n\n{VERDICT_APPROVED}"

    with patch("auto_dev_loop.multi_model.agent_query", side_effect=mock_query):
        with patch("auto_dev_loop.multi_model.run_external_with_timeout", side_effect=mock_external):
            result = await multi_model_review(
                worktree=Path("/tmp/wt"),
                plan="plan",
                diff="diff",
                agents=_agents(),
                config=_config(),
            )

    assert result.verdict.approved is True


@pytest.mark.asyncio
async def test_one_rejection_means_rejected():
    async def mock_query(agent_def, prompt, worktree, config, **kw):
        return VERDICT_APPROVED

    async def mock_external(cmd, prompt, worktree, timeout):
        return f"## Feedback\nMissing tests\n\n{VERDICT_NEEDS_REVISION}"

    with patch("auto_dev_loop.multi_model.agent_query", side_effect=mock_query):
        with patch("auto_dev_loop.multi_model.run_external_with_timeout", side_effect=mock_external):
            result = await multi_model_review(
                worktree=Path("/tmp/wt"),
                plan="plan",
                diff="diff",
                agents=_agents(),
                config=_config(),
            )

    assert result.verdict.approved is False
    assert "Missing tests" in result.verdict.feedback


@pytest.mark.asyncio
async def test_external_timeout_graceful():
    async def mock_query(agent_def, prompt, worktree, config, **kw):
        return VERDICT_APPROVED

    async def mock_external(cmd, prompt, worktree, timeout):
        raise asyncio.TimeoutError()

    with patch("auto_dev_loop.multi_model.agent_query", side_effect=mock_query):
        with patch("auto_dev_loop.multi_model.run_external_with_timeout", side_effect=mock_external):
            result = await multi_model_review(
                worktree=Path("/tmp/wt"),
                plan="plan",
                diff="diff",
                agents=_agents(),
                config=_config(),
            )

    # Claude approved, external timed out — should still be approved
    assert result.verdict.approved is True


@pytest.mark.asyncio
async def test_all_reviewers_fail():
    async def mock_query(agent_def, prompt, worktree, config, **kw):
        raise RuntimeError("SDK down")

    async def mock_external(cmd, prompt, worktree, timeout):
        raise asyncio.TimeoutError()

    with patch("auto_dev_loop.multi_model.agent_query", side_effect=mock_query):
        with patch("auto_dev_loop.multi_model.run_external_with_timeout", side_effect=mock_external):
            with pytest.raises(AllReviewersFailedError):
                await multi_model_review(
                    worktree=Path("/tmp/wt"),
                    plan="plan",
                    diff="diff",
                    agents=_agents(),
                    config=_config(),
                )


@pytest.mark.asyncio
async def test_stage_reviewers_override_config():
    """When reviewers_override is provided, only those reviewers are used."""
    called_external = []

    async def mock_query(agent_def, prompt, worktree, config, **kw):
        return VERDICT_APPROVED

    async def mock_external(cmd, prompt, worktree, timeout):
        called_external.append(cmd)
        return VERDICT_APPROVED

    with patch("auto_dev_loop.multi_model.agent_query", side_effect=mock_query):
        with patch("auto_dev_loop.multi_model.run_external_with_timeout", side_effect=mock_external):
            result = await multi_model_review(
                worktree=Path("/tmp/wt"),
                plan="plan",
                diff="diff",
                agents=_agents(),
                config=_config(),
                reviewers_override=["codex"],
            )

    assert result.verdict.approved is True
    # Only "codex" should have been called, not "gemini" from config
    assert called_external == ["codex"]


@pytest.mark.asyncio
async def test_stage_reviewers_override_empty_uses_config():
    """When reviewers_override is empty, fall back to config.defaults.external_reviewers."""
    called_external = []

    async def mock_query(agent_def, prompt, worktree, config, **kw):
        return VERDICT_APPROVED

    async def mock_external(cmd, prompt, worktree, timeout):
        called_external.append(cmd)
        return VERDICT_APPROVED

    with patch("auto_dev_loop.multi_model.agent_query", side_effect=mock_query):
        with patch("auto_dev_loop.multi_model.run_external_with_timeout", side_effect=mock_external):
            await multi_model_review(
                worktree=Path("/tmp/wt"),
                plan="plan",
                diff="diff",
                agents=_agents(),
                config=_config(),
                reviewers_override=[],
            )

    # Empty override -> fall back to config (which has ["gemini"])
    assert called_external == ["gemini"]


@pytest.mark.asyncio
async def test_stage_reviewers_claude_filtered_from_external():
    """'claude' in reviewers_override is filtered — it runs via agent_query, not subprocess."""
    called_external = []

    async def mock_query(agent_def, prompt, worktree, config, **kw):
        return VERDICT_APPROVED

    async def mock_external(cmd, prompt, worktree, timeout):
        called_external.append(cmd)
        return VERDICT_APPROVED

    with patch("auto_dev_loop.multi_model.agent_query", side_effect=mock_query):
        with patch("auto_dev_loop.multi_model.run_external_with_timeout", side_effect=mock_external):
            result = await multi_model_review(
                worktree=Path("/tmp/wt"),
                plan="plan",
                diff="diff",
                agents=_agents(),
                config=_config(),
                reviewers_override=["claude", "codex"],
            )

    assert result.verdict.approved is True
    # "claude" should NOT appear in external calls — it's the internal reviewer
    assert called_external == ["codex"]


@pytest.mark.asyncio
async def test_stage_reviewers_claude_only_no_external():
    """When reviewers_override=['claude'], zero external subprocesses should run."""
    called_external = []

    async def mock_query(agent_def, prompt, worktree, config, **kw):
        return VERDICT_APPROVED

    async def mock_external(cmd, prompt, worktree, timeout):
        called_external.append(cmd)
        return VERDICT_APPROVED

    with patch("auto_dev_loop.multi_model.agent_query", side_effect=mock_query):
        with patch("auto_dev_loop.multi_model.run_external_with_timeout", side_effect=mock_external):
            result = await multi_model_review(
                worktree=Path("/tmp/wt"),
                plan="plan",
                diff="diff",
                agents=_agents(),
                config=_config(),
                reviewers_override=["claude"],
            )

    assert result.verdict.approved is True
    assert called_external == []
    assert len(result.individual) == 1
