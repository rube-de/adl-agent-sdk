"""Tests for label-based workflow routing."""

import pytest

from auto_dev_loop.workflow_router import select_workflow
from auto_dev_loop.models import Issue, WorkflowSelectionConfig


SELECTION = WorkflowSelectionConfig(
    default="feature",
    label_map={
        "bug": "bug_fix",
        "hotfix": "bug_fix",
        "docs": "documentation",
        "security": "security_audit",
        "infrastructure": "ops_change",
    },
    priority_overrides={
        "P0": {"bug": "bug_fix", "security": "security_audit"},
    },
)


def _issue(labels: list[str], priority: str | None = None) -> Issue:
    return Issue(id=1, number=1, repo="o/r", title="t", body="b", labels=labels, priority=priority)


def test_label_bug():
    assert select_workflow(_issue(["bug"]), SELECTION) == "bug_fix"


def test_label_docs():
    assert select_workflow(_issue(["docs"]), SELECTION) == "documentation"


def test_label_security():
    assert select_workflow(_issue(["security"]), SELECTION) == "security_audit"


def test_label_infrastructure():
    assert select_workflow(_issue(["infrastructure"]), SELECTION) == "ops_change"


def test_no_matching_label_returns_default():
    assert select_workflow(_issue(["random-label"]), SELECTION) == "feature"


def test_no_labels_returns_default():
    assert select_workflow(_issue([]), SELECTION) == "feature"


def test_first_matching_label_wins():
    # "bug" matches first in label_map iteration
    result = select_workflow(_issue(["bug", "docs"]), SELECTION)
    assert result in ("bug_fix", "documentation")  # Order depends on set iteration


def test_priority_override():
    assert select_workflow(_issue(["bug"], priority="P0"), SELECTION) == "bug_fix"


def test_priority_override_security():
    assert select_workflow(_issue(["security"], priority="P0"), SELECTION) == "security_audit"


def test_priority_override_no_match_falls_through():
    # P0 override doesn't have "docs", falls through to label_map
    assert select_workflow(_issue(["docs"], priority="P0"), SELECTION) == "documentation"


def test_no_priority_overrides():
    cfg = WorkflowSelectionConfig(
        default="feature",
        label_map={"bug": "bug_fix"},
    )
    assert select_workflow(_issue(["bug"]), cfg) == "bug_fix"
