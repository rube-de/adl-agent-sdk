"""Common Agent SDK query wrapper — model resolution, hooks, streaming."""

from __future__ import annotations

import logging
from pathlib import Path

from .hooks import block_destructive
from .models import AgentDef, Config, Issue
from .model_roles import resolve_model

log = logging.getLogger(__name__)


def build_query_options(
    agent_def: AgentDef,
    worktree: Path,
    config: Config,
) -> dict:
    """Build options dict for SDK query() call."""
    model = resolve_model(agent_def.model_role, config.model_roles)
    return {
        "system_prompt": agent_def.system_prompt,
        "allowed_tools": agent_def.tools,
        "cwd": str(worktree),
        "permission_mode": "bypassPermissions",
        "max_turns": agent_def.max_turns,
        "model": model,
        "hooks": {"bash_safety": block_destructive},
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
) -> str:
    """Run a single agent query via the Claude Agent SDK."""
    from claude_agent_sdk import query  # defer import for testability

    opts = build_query_options(agent_def, worktree, config)
    result_parts: list[str] = []

    async for msg in query(prompt=prompt, **opts):
        text = extract_text(msg)
        if text:
            result_parts.append(text)

    return "".join(result_parts)
