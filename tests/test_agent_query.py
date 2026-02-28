"""Tests for the common Agent SDK query wrapper."""

from pathlib import Path
from unittest.mock import patch

import pytest

from auto_dev_loop.agent_query import agent_query, build_query_options
from auto_dev_loop.models import AgentDef, Config, TelegramConfig


def _agent():
    return AgentDef(
        name="tester",
        description="Test runner",
        system_prompt="You are a test runner.",
        tools=["Bash", "Read"],
        model_role="smol",
        max_turns=30,
    )


def _config():
    return Config(
        telegram=TelegramConfig(bot_token="t", chat_id=1),
        model_roles={"smol": "claude-haiku-4-5", "default": "claude-sonnet-4-5"},
        repos=[],
    )


def test_build_query_options():
    agent = _agent()
    opts = build_query_options(agent, Path("/tmp/worktree"), _config())
    assert opts["system_prompt"] == "You are a test runner."
    assert opts["cwd"] == "/tmp/worktree"
    assert opts["max_turns"] == 30
    assert "Bash" in opts["allowed_tools"]


def test_build_query_options_resolves_model():
    agent = _agent()
    opts = build_query_options(agent, Path("/tmp/wt"), _config())
    assert opts["model"] == "claude-haiku-4-5"


def test_build_query_options_default_role_fallback():
    agent = AgentDef(
        name="x", description="", system_prompt="p",
        tools=[], model_role="unknown_role", max_turns=10,
    )
    opts = build_query_options(agent, Path("/tmp/wt"), _config())
    assert opts["model"] == "claude-sonnet-4-5"


def test_build_query_options_has_bash_hook():
    agent = _agent()
    opts = build_query_options(agent, Path("/tmp/wt"), _config())
    assert "hooks" in opts
    assert "bash_safety" in opts["hooks"]


@pytest.mark.asyncio
async def test_agent_query_collects_text():
    agent = _agent()

    async def fake_query(prompt, **kwargs):
        for chunk in [{"type": "text", "text": "Hello "}, {"type": "text", "text": "world"}]:
            yield chunk

    with patch("claude_agent_sdk.query", side_effect=fake_query, create=True):
        result = await agent_query(
            agent_def=agent,
            prompt="run tests",
            worktree=Path("/tmp/wt"),
            config=_config(),
        )
    assert "Hello" in result
    assert "world" in result
