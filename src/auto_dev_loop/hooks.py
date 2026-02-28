"""Bash safety hooks for blocking destructive commands.

Used via SDK HookMatcher to intercept Bash tool calls.
"""

from __future__ import annotations

import re

BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|--recursive)\b"),  # rm -rf, rm --recursive
    re.compile(r"\bgit\s+push\s+.*(-f|--force)\b"),                 # git push --force
    re.compile(r"\bgit\s+reset\s+--hard\b"),                        # git reset --hard
    re.compile(r"\bgit\s+clean\s+-[a-zA-Z]*f\b"),                   # git clean -f
    re.compile(r"\bDROP\s+(TABLE|DATABASE)\b", re.IGNORECASE),      # DROP TABLE/DATABASE
    re.compile(r"\bchmod\s+(-[a-zA-Z]*R|--recursive)\s+777\b"),     # chmod -R 777
    re.compile(r"\b(pkill|kill)\s+-9\b"),                            # kill -9
    re.compile(r"\bmkfs\b"),                                         # mkfs
    re.compile(r"\bdd\s+if="),                                       # dd (disk destroy)
]


def is_destructive_command(command: str) -> bool:
    """Check if a command matches any blocked pattern."""
    return any(pattern.search(command) for pattern in BLOCKED_PATTERNS)


def block_destructive(tool_input: dict) -> dict | None:
    """SDK PreToolUse hook callback. Returns error dict to block, None to allow."""
    command = tool_input.get("command", "")
    if is_destructive_command(command):
        return {
            "error": f"Blocked destructive command: {command[:100]}",
            "type": "safety_block",
        }
    return None
