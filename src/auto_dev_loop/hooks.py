"""Bash safety hooks for blocking destructive commands.

Used via SDK HookMatcher to intercept Bash tool calls.

Implements an allowlist-first approach: commands must match a known-safe prefix,
then are checked against blocked patterns for destructive variants.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass
class SecurityEvent:
    command: str
    reason: str
    blocked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SecurityEventHandler(Protocol):
    def on_command_blocked(self, event: SecurityEvent) -> None: ...


class LoggingSecurityHandler:
    """Implements SecurityEventHandler by emitting a warning log."""

    def on_command_blocked(self, event: SecurityEvent) -> None:
        logger.warning(
            "Command blocked | reason=%s | command=%r | at=%s",
            event.reason,
            event.command,
            event.blocked_at.isoformat(),
        )


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_ALLOWED_PREFIXES: list[str] = [
    # VCS
    "git",
    "gh",
    # Testing / Python
    "pytest",
    "python -m pytest",
    "python",
    "python3",
    "pip",
    "uv",
    # Search / navigation
    "rg",
    "fd",
    "find",
    "grep",
    "which",
    "pwd",
    "env",
    # File ops (read-only flavour — write ops are allowed but caught by patterns)
    "cat",
    "bat",
    "head",
    "tail",
    "ls",
    "eza",
    "stat",
    "file",
    "tree",
    "diff",
    "wc",
    "sort",
    "uniq",
    "dirname",
    "basename",
    "realpath",
    # File mutation (safe subset; destructive variants caught by patterns)
    "mkdir",
    "cp",
    "mv",
    "touch",
    "echo",
    "tee",
    "sed",
    "awk",
    # JS / Node ecosystem
    "node",
    "npm",
    "npx",
    "bun",
    # Rust
    "cargo",
    # Build / task runners
    "make",
    "just",
    # Data / config processing
    "jq",
    "yq",
    # Process / shell builtins
    "cd",
    "true",
    "false",
    "test",
    "[",
    # Misc safe utils
    "chmod",
    "chown",
]

_BLOCKED_PATTERNS: list[re.Pattern] = [
    # rm with any combination of -r/-R and -f in flags, and --recursive
    # Catches: rm -rf, rm -fr, rm -r -f, rm -f -r, rm --recursive, rm -r, etc.
    re.compile(
        r"\brm\b"
        r"(?:"
        r"\s+(?:-[a-zA-Z]*[rR][a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*|--recursive|--force)"
        r")+",
        re.IGNORECASE,
    ),
    # git push --force / -f (no \b before flags — '-' is not a word char)
    re.compile(r"\bgit\s+push\b.*\s(-f|--force)\b"),
    # git reset --hard
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    # git clean -f (and variants)
    re.compile(r"\bgit\s+clean\b.*-[a-zA-Z]*f"),
    # DROP TABLE / DATABASE (SQL)
    re.compile(r"\bDROP\s+(TABLE|DATABASE)\b", re.IGNORECASE),
    # chmod -R 777 (recursive world-writable)
    re.compile(r"\bchmod\b.*\s(-R|--recursive)\b.*\b777\b", re.IGNORECASE),
    # kill -9 / pkill -9
    re.compile(r"\b(pkill|kill)\s+-9\b"),
    # mkfs (disk formatting)
    re.compile(r"\bmkfs\b"),
    # dd if= (disk-level write)
    re.compile(r"\bdd\s+if="),
    # Pipe to shell: curl/wget ... | ... sh/bash/zsh/fish
    re.compile(
        r"\b(curl|wget)\b.+\|\s*(?:[a-z]+\s+)?(?:sh|bash|zsh|fish)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    # Inline Python execution: python -c / python3 -c
    re.compile(r"\bpython3?\s+-c\b"),
]


# ---------------------------------------------------------------------------
# CommandGuard
# ---------------------------------------------------------------------------


class CommandGuard:
    """Allowlist-first command filter for SDK PreToolUse hooks.

    Flow:
      1. If the command doesn't start with any allowed prefix → block.
      2. If the command matches a blocked pattern (even after passing step 1) → block.
      3. Otherwise → allow (return None).
    """

    def __init__(
        self,
        on_block: SecurityEventHandler | None = None,
        allowed_prefixes: list[str] | None = None,
        blocked_patterns: list[re.Pattern] | None = None,
    ) -> None:
        self._handler = on_block
        self._allowed_prefixes: list[str] = (
            allowed_prefixes if allowed_prefixes is not None else list(_ALLOWED_PREFIXES)
        )
        self._blocked_patterns: list[re.Pattern] = (
            blocked_patterns if blocked_patterns is not None else list(_BLOCKED_PATTERNS)
        )
        self._events: list[SecurityEvent] = []

    # -- public interface ---------------------------------------------------

    @property
    def events(self) -> list[SecurityEvent]:
        """Accumulated blocked events since last drain."""
        return list(self._events)

    def drain_events(self) -> list[SecurityEvent]:
        """Return and clear accumulated events."""
        events, self._events = self._events, []
        return events

    def __call__(self, tool_input: dict) -> dict | None:
        """PreToolUse hook entry point. Returns error dict to block, None to allow."""
        command: str = tool_input.get("command", "").strip()

        # 1. Allowlist check
        if not self._is_allowed(command):
            return self._block(command, "command prefix not in allowlist")

        # 2. Blocked-pattern check
        for pattern in self._blocked_patterns:
            if pattern.search(command):
                return self._block(command, f"matched blocked pattern: {pattern.pattern!r}")

        return None

    # -- internals ----------------------------------------------------------

    def _is_allowed(self, command: str) -> bool:
        lower = command.lower()
        return any(lower == prefix or lower.startswith(prefix + " ") for prefix in self._allowed_prefixes)

    def _block(self, command: str, reason: str) -> dict:
        event = SecurityEvent(command=command, reason=reason)
        self._events.append(event)
        logger.warning("Blocked command | reason=%s | command=%r", reason, command[:120])
        if self._handler is not None:
            self._handler.on_command_blocked(event)
        return {
            "error": f"Blocked: {reason} — {command[:100]}",
            "type": "safety_block",
        }


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def create_default_guard(handler: SecurityEventHandler | None = None) -> CommandGuard:
    """Return a CommandGuard with the project-standard defaults."""
    return CommandGuard(on_block=handler)
