"""Message builders for Telegram progress, escalation, and notifications."""

from __future__ import annotations

import html

from ..models import Issue, StageState, StageStatus
from ..workflow_loader import WorkflowConfig, StageConfig
from .callbacks import encode_callback
from .models import InlineKeyboardButton, InlineKeyboardMarkup


def build_progress_message(
    issue: Issue,
    workflow: WorkflowConfig,
    stage_states: dict[str, StageState],
    total_elapsed: str,
) -> str:
    """Build live-updating progress card. Returns HTML."""
    repo = html.escape(issue.repo)
    wf_id = html.escape(workflow.id)
    lines = [f"<b>{repo} #{issue.number}</b> — {wf_id}\n"]

    for stage in workflow.stages:
        state = stage_states.get(stage.ref)
        if state and state.status in (StageStatus.APPROVED, StageStatus.COMPLETED):
            lines.append(f"  ✅ <code>{stage.ref:<16}</code> ({state.elapsed})")
        elif state and state.status == StageStatus.RUNNING:
            detail = ""
            if stage.type == "team":
                detail = f"cycle {state.iteration}/{stage.maxIterations} "
            lines.append(f"  ⏳ <code>{stage.ref:<16}</code> {detail}({state.elapsed})")
        elif state and state.status in (StageStatus.VETOED, StageStatus.ESCALATED):
            lines.append(f"  🔴 <code>{stage.ref:<16}</code> ({state.elapsed})")
        elif stage.optional:
            lines.append(f"  ⬜ <code>{stage.ref:<16}</code> (optional)")
        else:
            lines.append(f"  ⬜ <code>{stage.ref}</code>")

    lines.append(f"\nElapsed: {total_elapsed}")
    return "\n".join(lines)


def build_escalation_message(
    issue: Issue,
    stage: StageConfig,
    verdict: object,
    reason: str,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build escalation card with inline action buttons. Returns (HTML, keyboard)."""
    emoji = "🔒" if reason == "security_veto" else "⚠️"
    title = reason.replace("_", " ").title()

    raw_feedback = getattr(verdict, "feedback", None) or "No feedback provided."
    feedback = html.escape(raw_feedback)
    iteration = getattr(verdict, "iteration", 1)
    repo = html.escape(issue.repo)

    text = (
        f"{emoji} <b>{title}</b> — {repo} #{issue.number}\n\n"
        f"Stage: <code>{html.escape(stage.ref)}</code>\n"
        f"Iteration: {iteration}/{stage.maxIterations}\n\n"
        f"<blockquote>{feedback}</blockquote>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Approve",
            callback_data=encode_callback("approve", issue.id, stage.ref),
        ),
        InlineKeyboardButton(
            text="❌ Reject",
            callback_data=encode_callback("reject", issue.id, stage.ref),
        ),
        InlineKeyboardButton(
            text="💬 Reply",
            callback_data=encode_callback("feedback", issue.id, stage.ref),
        ),
    ]])

    return text, keyboard


def build_completion_message(issue: Issue, pr_url: str) -> str:
    """Build PR-created notification."""
    repo = html.escape(issue.repo)
    safe_url = html.escape(pr_url)
    return (
        f"✅ <b>PR Created</b> — {repo} #{issue.number}\n\n"
        f'<a href="{safe_url}">{safe_url}</a>'
    )


def build_error_message(issue: Issue, error: str) -> str:
    """Build error notification."""
    repo = html.escape(issue.repo)
    safe_error = html.escape(error[:500])
    return (
        f"🔥 <b>Error</b> — {repo} #{issue.number}\n\n"
        f"<code>{safe_error}</code>"
    )


def build_security_message(
    issue: Issue | None,
    blocked_commands: list[dict],
) -> str:
    """Build security alert for blocked commands. Returns HTML."""
    count = len(blocked_commands)
    header = f"🛡 <b>Security Alert</b> — {count} command{'s' if count != 1 else ''} blocked"
    if issue:
        header += f" ({issue.repo} #{issue.number})"

    lines = [header, ""]
    for entry in blocked_commands[:5]:  # cap at 5 to avoid message size limits
        cmd = html.escape(entry.get("command", "")[:80])
        reason = html.escape(entry.get("reason", "unknown"))
        lines.append(f"<code>{cmd}</code>")
        lines.append(f"  ↳ {reason}")
        lines.append("")

    if count > 5:
        lines.append(f"<i>…and {count - 5} more</i>")

    return "\n".join(lines)
