"""Tests for the PR review loop — iterates on PR review comments."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from auto_dev_loop.review_loop import (
    push_fixes,
    review_loop,
    MaxReviewCyclesError,
    PushFixesError,
    ReviewLoopResult,
)
from auto_dev_loop.models import Issue, AgentDef, Config, TelegramConfig, Defaults
from auto_dev_loop.pr_status import PrStatus


def _issue():
    return Issue(id=1, number=42, repo="owner/repo", title="Fix bug", body="Crashes")


def _config(max_cycles=5):
    return Config(
        telegram=TelegramConfig(bot_token="t", chat_id=1),
        model_roles={"default": "claude-sonnet-4-5"},
        repos=[],
        defaults=Defaults(max_review_cycles=max_cycles, review_backoff=[1, 2, 3]),
    )


def _agents():
    return {
        "pr_fixer": AgentDef(
            name="pr_fixer", description="", system_prompt="fix PR",
            tools=["Bash", "Read", "Edit"], model_role="default", max_turns=50,
        ),
    }


@pytest.mark.asyncio
async def test_review_loop_already_approved():
    status = PrStatus(
        state="OPEN", mergeable="MERGEABLE",
        review_approved=True, ci_passing=True,
    )

    with patch("auto_dev_loop.review_loop.check_pr_status", return_value=status):
        result = await review_loop(_issue(), 1, Path("/tmp/wt"), _config())

    assert result.cycles == 0
    assert result.merged is False


@pytest.mark.asyncio
async def test_review_loop_fixes_then_approved():
    call_count = {"status": 0}

    async def mock_status(repo, pr_number):
        call_count["status"] += 1
        if call_count["status"] == 1:
            return PrStatus(
                state="OPEN", mergeable="MERGEABLE",
                review_approved=False, ci_passing=True,
            )
        return PrStatus(
            state="OPEN", mergeable="MERGEABLE",
            review_approved=True, ci_passing=True,
        )

    async def mock_comments(repo, pr_number):
        return [{"author": {"login": "rev"}, "body": "fix this", "path": "a.py", "line": 1, "state": "PENDING"}]

    async def mock_query(agent_def, prompt, worktree, config, **kw):
        return "Fixed the issue"

    with patch("auto_dev_loop.review_loop.check_pr_status", side_effect=mock_status):
        with patch("auto_dev_loop.review_loop.fetch_pr_comments", side_effect=mock_comments):
            with patch("auto_dev_loop.review_loop.load_agents", return_value=_agents()):
                with patch("auto_dev_loop.review_loop.agent_query", side_effect=mock_query):
                    with patch("auto_dev_loop.review_loop.push_fixes", new_callable=AsyncMock):
                        with patch("auto_dev_loop.review_loop.asyncio.sleep", new_callable=AsyncMock):
                            result = await review_loop(_issue(), 1, Path("/tmp/wt"), _config())

    assert result.cycles == 1


@pytest.mark.asyncio
async def test_review_loop_max_cycles():
    status = PrStatus(
        state="OPEN", mergeable="MERGEABLE",
        review_approved=False, ci_passing=True,
    )

    async def mock_comments(repo, pr_number):
        return [{"author": {"login": "rev"}, "body": "still wrong", "path": "a.py", "line": 1, "state": "PENDING"}]

    async def mock_query(agent_def, prompt, worktree, config, **kw):
        return "Tried to fix"

    with patch("auto_dev_loop.review_loop.check_pr_status", return_value=status):
        with patch("auto_dev_loop.review_loop.fetch_pr_comments", side_effect=mock_comments):
            with patch("auto_dev_loop.review_loop.load_agents", return_value=_agents()):
                with patch("auto_dev_loop.review_loop.agent_query", side_effect=mock_query):
                    with patch("auto_dev_loop.review_loop.push_fixes", new_callable=AsyncMock):
                        with patch("auto_dev_loop.review_loop.asyncio.sleep", new_callable=AsyncMock):
                            with pytest.raises(MaxReviewCyclesError):
                                await review_loop(_issue(), 1, Path("/tmp/wt"), _config(max_cycles=2))


# ── push_fixes return-code tests ────────────────────────────────────


def _mock_proc(returncode: int, stderr: str = ""):
    """Create a mock subprocess with a preset returncode."""
    proc = AsyncMock()
    proc.communicate.return_value = (b"", stderr.encode())
    proc.returncode = returncode
    return proc


@pytest.mark.asyncio
async def test_push_fixes_success():
    procs = [_mock_proc(0), _mock_proc(0), _mock_proc(0)]
    with patch("auto_dev_loop.review_loop.asyncio.create_subprocess_exec", side_effect=procs):
        result = await push_fixes(Path("/tmp/wt"), _issue())
    assert result is True


@pytest.mark.asyncio
async def test_push_fixes_nothing_to_commit():
    """git commit returns 1 when nothing to commit — should return False, not raise."""
    procs = [_mock_proc(0), _mock_proc(1, "nothing to commit")]
    with patch("auto_dev_loop.review_loop.asyncio.create_subprocess_exec", side_effect=procs):
        result = await push_fixes(Path("/tmp/wt"), _issue())
    assert result is False


@pytest.mark.asyncio
async def test_push_fixes_git_add_fails():
    procs = [_mock_proc(128, "fatal: not a git repository")]
    with patch("auto_dev_loop.review_loop.asyncio.create_subprocess_exec", side_effect=procs):
        with pytest.raises(PushFixesError, match="git add failed"):
            await push_fixes(Path("/tmp/wt"), _issue())


@pytest.mark.asyncio
async def test_push_fixes_git_push_fails():
    procs = [_mock_proc(0), _mock_proc(0), _mock_proc(1, "rejected")]
    with patch("auto_dev_loop.review_loop.asyncio.create_subprocess_exec", side_effect=procs):
        with pytest.raises(PushFixesError, match="git push failed"):
            await push_fixes(Path("/tmp/wt"), _issue())
