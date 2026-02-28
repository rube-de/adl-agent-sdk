"""Message builders for Telegram progress, escalation, and notifications."""

from __future__ import annotations

from ..models import Issue, StageState
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
    lines = [f"<b>{issue.repo} #{issue.number}</b> — {workflow.id}\n"]

    for stage in workflow.stages:
        state = stage_states.get(stage.ref)
        if state and state.status in ("approved", "completed"):
            lines.append(f"  ✅ <code>{stage.ref:<16}</code> ({state.elapsed})")
        elif state and state.status == "running":
            detail = ""
            if stage.type == "team":
                detail = f"cycle {state.iteration}/{stage.maxIterations} "
            lines.append(f"  ⏳ <code>{stage.ref:<16}</code> {detail}({state.elapsed})")
        elif state and state.status in ("vetoed", "escalated"):
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

    feedback = getattr(verdict, "feedback", None) or "No feedback provided."
    iteration = getattr(verdict, "iteration", 1)

    text = (
        f"{emoji} <b>{title}</b> — {issue.repo} #{issue.number}\n\n"
        f"Stage: <code>{stage.ref}</code>\n"
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
    return (
        f"✅ <b>PR Created</b> — {issue.repo} #{issue.number}\n\n"
        f'<a href="{pr_url}">{pr_url}</a>'
    )


def build_error_message(issue: Issue, error: str) -> str:
    """Build error notification."""
    return (
        f"🔥 <b>Error</b> — {issue.repo} #{issue.number}\n\n"
        f"<code>{error[:500]}</code>"
    )
