"""Common Agent SDK query wrapper — model resolution, hooks, streaming."""

from __future__ import annotations

import logging
from pathlib import Path

from .hooks import CommandGuard, create_default_guard
from .models import AgentDef, Config, Issue
from .model_roles import resolve_model

log = logging.getLogger(__name__)


def build_query_options(
    agent_def: AgentDef,
    worktree: Path,
    config: Config,
    guard: CommandGuard | None = None,
) -> dict:
    """Build options dict for SDK query() call."""
    model = resolve_model(agent_def.model_role, config.model_roles)
    _guard = guard or create_default_guard()
    return {
        "system_prompt": agent_def.system_prompt,
        "allowed_tools": agent_def.tools,
        "cwd": str(worktree),
        "permission_mode": "default",
        "max_turns": agent_def.max_turns,
        "model": model,
        "hooks": {"bash_safety": _guard},
    }


def extract_text(msg: dict) -> str:
    """Extract text content from an SDK message."""
    if isinstance(msg, dict) and msg.get("type") == "text":
        return msg.get("text", "")
    return ""


async def agent_query(
    agent_def: AgentDef,
    prompt: str,
    worktree: Path,
    config: Config,
    issue: Issue | None = None,
    guard: CommandGuard | None = None,
) -> str:
    """Run a single agent query via the Claude Agent SDK."""
    from claude_agent_sdk import query  # defer import for testability

    opts = build_query_options(agent_def, worktree, config, guard=guard)
    result_parts: list[str] = []

    async for msg in query(prompt=prompt, **opts):
        text = extract_text(msg)
        if text:
            result_parts.append(text)

    return "".join(result_parts)
