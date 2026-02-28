"""Named conditions for workflow stage evaluation.

Kept in its own module to avoid circular imports between workflow_engine
(which imports StageConfig from workflow_loader) and workflow_loader
(which needs to validate condition names).

Adding a new condition requires both a callable here and an entry in CONDITIONS.
"""

from __future__ import annotations

from collections.abc import Callable

from .models import Issue


def _unknowns_exist(issue: Issue) -> bool:
    return (
        "?" in issue.body
        or any(
            w in issue.body.lower()
            for w in ("unclear", "unknown", "investigate", "explore")
        )
    )


def _security_relevant(issue: Issue) -> bool:
    return (
        "security" in issue.labels
        or any(
            w in issue.body.lower()
            for w in ("auth", "crypto", "permissions", "cve", "vulnerability")
        )
    )


def _deployment_needed(issue: Issue) -> bool:
    return any(label in issue.labels for label in ("deploy", "ops", "infrastructure"))


def _code_review_needed(issue: Issue) -> bool:
    return True


CONDITIONS: dict[str, Callable[[Issue], bool]] = {
    "unknowns_exist": _unknowns_exist,
    "security_relevant": _security_relevant,
    "deployment_needed": _deployment_needed,
    "code_review_needed": _code_review_needed,
}
