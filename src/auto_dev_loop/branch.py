"""Branch name utilities. Single source of truth for ADL branch naming (F25)."""

from __future__ import annotations

import re

from .models import Issue

_MAX_SLUG_LENGTH = 60


def _sanitize_slug(raw: str) -> str:
    """Sanitize a string for use as a branch name slug.

    - Replace any character not in [a-zA-Z0-9._-] with a dash
    - Collapse consecutive dots (prevent path traversal via "..")
    - Collapse consecutive dashes
    - Strip leading/trailing dashes and dots
    - Enforce max length
    - Fallback to "issue" if result is empty
    """
    slug = re.sub(r"[^a-zA-Z0-9._-]", "-", raw).lower()
    slug = re.sub(r"\.{2,}", ".", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-.")
    slug = slug[:_MAX_SLUG_LENGTH].rstrip("-.")
    while slug.endswith(".lock"):
        slug = slug.removesuffix(".lock").rstrip("-.")
    return slug or "issue"


def build_branch_name(issue: Issue) -> str:
    """Build a deterministic, path-safe branch name from an issue."""
    slug = _sanitize_slug(issue.title)
    return f"adl/{issue.number}-{slug}"
