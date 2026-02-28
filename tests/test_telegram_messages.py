"""Tests for Telegram message builders."""

from auto_dev_loop.telegram.messages import (
    build_progress_message,
    build_escalation_message,
    build_completion_message,
    build_error_message,
)
from auto_dev_loop.models import Issue, StageState
from auto_dev_loop.workflow_loader import WorkflowConfig, StageConfig


def _issue():
    return Issue(id=1, number=42, repo="owner/repo", title="Fix bug", body="b")


def _workflow():
    return WorkflowConfig(id="bug_fix", description="", stages=[
        StageConfig(ref="plan", agent="architect"),
        StageConfig(ref="dev", agent="orchestrator", type="team"),
        StageConfig(ref="review", agent="reviewer"),
    ])


def test_progress_message_running():
    states = {"plan": StageState(status="approved", elapsed="12s")}
    text = build_progress_message(_issue(), _workflow(), states, "12s")
    assert "owner/repo #42" in text
    assert "bug_fix" in text
    assert "plan" in text


def test_progress_message_all_pending():
    text = build_progress_message(_issue(), _workflow(), {}, "0s")
    assert "plan" in text
    assert "dev" in text
    assert "review" in text


def test_escalation_message_has_buttons():
    stage = StageConfig(ref="security", agent="sec", canVeto=True)

    class FakeVerdict:
        feedback = "Security risk detected"
        iteration = 1

    text, keyboard = build_escalation_message(
        _issue(), stage, FakeVerdict(), "security_veto",
    )
    assert "Security Veto" in text
    assert "owner/repo #42" in text
    assert len(keyboard.inline_keyboard[0]) == 3  # Approve, Reject, Reply


def test_completion_message():
    text = build_completion_message(_issue(), "https://github.com/o/r/pull/1")
    assert "PR Created" in text
    assert "https://github.com/o/r/pull/1" in text


def test_error_message():
    text = build_error_message(_issue(), "Something went wrong")
    assert "Error" in text
    assert "Something went wrong" in text


def test_error_message_truncates():
    long_error = "x" * 1000
    text = build_error_message(_issue(), long_error)
    assert len(text) < 1500
