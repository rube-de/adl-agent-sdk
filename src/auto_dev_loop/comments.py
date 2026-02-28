"""PR review comments extraction and formatting."""

from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger(__name__)


def parse_review_comments(raw_comments: list[dict]) -> list[dict]:
    return [
        {
            "author": c.get("author", {}).get("login", "unknown"),
            "body": c.get("body", ""),
            "path": c.get("path"),
            "line": c.get("line"),
            "state": c.get("state"),
        }
        for c in raw_comments
    ]


def filter_actionable(comments: list[dict]) -> list[dict]:
    return [c for c in comments if c.get("path")]


def format_for_agent(comments: list[dict]) -> str:
    parts = []
    for c in comments:
        loc = f"{c['path']}:{c['line']}" if c.get("line") else c.get("path", "general")
        parts.append(f"**{loc}** ({c['author']}):\n{c['body']}")
    return "\n\n---\n\n".join(parts)


async def fetch_pr_comments(repo: str, pr_number: int) -> list[dict]:
    """Fetch PR review comments via gh CLI."""
    proc = await asyncio.create_subprocess_exec(
        "gh", "api",
        f"repos/{repo}/pulls/{pr_number}/comments",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.error(f"Failed to fetch PR comments: {stderr.decode()}")
        return []
    return json.loads(stdout)
