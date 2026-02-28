"""Branch name utilities. Single source of truth for ADL branch naming (F25)."""

from __future__ import annotations

from .models import Issue


def build_branch_name(issue: Issue) -> str:
    """Build a deterministic branch name from an issue."""
    slug = issue.title[:30].replace(" ", "-").lower()
    return f"adl/{issue.number}-{slug}"
