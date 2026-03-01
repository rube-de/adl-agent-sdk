"""Structured review verdict parsing.

Reviewers must end their response with APPROVED or NEEDS_REVISION on its own line.
Conservative: no marker = treated as needs revision.
"""

from __future__ import annotations

import re

from .models import ReviewVerdict


def parse_review_verdict(output: str) -> ReviewVerdict:
    """Parse a review verdict from agent output."""
    if not output.strip():
        return ReviewVerdict(approved=False, feedback=output or None)

    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]

    # Check all non-empty lines bottom-up for markers
    for line in reversed(lines):
        if line == "APPROVED":
            return ReviewVerdict(approved=True, feedback=None)
        if line == "NEEDS_REVISION":
            # Extract feedback section if present
            match = re.search(
                r"## Feedback\s*\n(.*?)(?=\nNEEDS_REVISION)",
                output,
                re.DOTALL,
            )
            feedback = match.group(1).strip() if match else output
            return ReviewVerdict(approved=False, feedback=feedback)

    # No marker found — conservative: treat as needs revision
    return ReviewVerdict(approved=False, feedback=output)


def synthesize_reviews(reviews: list[tuple[str, ReviewVerdict]]) -> ReviewVerdict:
    """Synthesize multiple review verdicts. Conservative: any rejection = reject."""
    if not reviews:
        return ReviewVerdict(approved=False, feedback=None)

    if all(r.approved for _, r in reviews):
        return ReviewVerdict(approved=True, feedback=None)

    feedback_parts = []
    for model, review in reviews:
        if not review.approved and review.feedback:
            feedback_parts.append(f"### {model}\n{review.feedback}")

    return ReviewVerdict(
        approved=False,
        feedback="\n\n".join(feedback_parts) if feedback_parts else None,
    )
