"""Tests for structured review verdict parsing."""

import pytest

from auto_dev_loop.review_parser import (
    parse_review_verdict,
    synthesize_reviews,
)
from auto_dev_loop.models import ReviewVerdict, VERDICT_APPROVED, VERDICT_NEEDS_REVISION, VERDICT_VETOED


class TestParseReviewVerdict:

    def test_approved_simple(self):
        v = parse_review_verdict(f"Everything looks good.\n\n{VERDICT_APPROVED}")
        assert v.approved is True
        assert v.feedback is None

    def test_approved_with_trailing_whitespace(self):
        v = parse_review_verdict(f"Good.\n\n{VERDICT_APPROVED}\n  \n")
        assert v.approved is True

    def test_needs_revision_with_feedback(self):
        output = f"""\
The code has issues.

## Feedback

1. Missing error handling in auth.py
2. No tests for edge case

{VERDICT_NEEDS_REVISION}"""
        v = parse_review_verdict(output)
        assert v.approved is False
        assert "Missing error handling" in v.feedback
        assert "No tests" in v.feedback

    def test_needs_revision_without_feedback_section(self):
        output = f"This code is bad.\n\n{VERDICT_NEEDS_REVISION}"
        v = parse_review_verdict(output)
        assert v.approved is False
        assert v.feedback == output

    def test_no_marker_defaults_to_rejected(self):
        output = "I reviewed the code and it has some issues."
        v = parse_review_verdict(output)
        assert v.approved is False
        assert v.feedback == output

    def test_empty_output(self):
        v = parse_review_verdict("")
        assert v.approved is False

    def test_approved_mid_text_not_matched(self):
        output = "The code is APPROVED by tests but NEEDS_REVISION for style."
        v = parse_review_verdict(output)
        # Last marker in last 5 lines wins
        assert v.approved is False

    def test_marker_must_be_on_own_line(self):
        output = f"Status: APPROVED\n\n{VERDICT_APPROVED}"
        v = parse_review_verdict(output)
        assert v.approved is True

    def test_vetoed(self):
        output = f"Security risk.\n\n{VERDICT_VETOED}"
        v = parse_review_verdict(output)
        assert v.approved is False
        assert v.feedback == output

    def test_approved_beyond_5_lines(self):
        """APPROVED keyword beyond last 5 lines should still be found."""
        verbose_lines = "\n".join(f"Detail line {i}" for i in range(10))
        output = f"Review complete.\n\n{VERDICT_APPROVED}\n{verbose_lines}"
        v = parse_review_verdict(output)
        assert v.approved is True

    def test_needs_revision_beyond_5_lines(self):
        """NEEDS_REVISION keyword beyond last 5 lines should still be found."""
        verbose_lines = "\n".join(f"Detail line {i}" for i in range(10))
        output = f"## Feedback\nFix the tests\n\n{VERDICT_NEEDS_REVISION}\n{verbose_lines}"
        v = parse_review_verdict(output)
        assert v.approved is False
        assert "Fix the tests" in v.feedback

    def test_bare_approved_not_matched_after_hardening(self):
        """After marker hardening, bare 'APPROVED' should not trigger approval."""
        output = "The PR status was APPROVED on GitHub.\nLooks good."
        v = parse_review_verdict(output)
        # No bracketed marker -> conservative rejection
        assert v.approved is False

    def test_injected_marker_in_code_ignored(self):
        """Markers embedded in code on the same line are ignored."""
        output = f"Changed: status = '{VERDICT_APPROVED}'\nNo real verdict here."
        v = parse_review_verdict(output)
        # Marker embedded in code, not on its own line
        assert v.approved is False


class TestSynthesizeReviews:

    def test_all_approved(self):
        reviews = [
            ("claude", ReviewVerdict(approved=True, feedback=None)),
            ("gemini", ReviewVerdict(approved=True, feedback=None)),
        ]
        result = synthesize_reviews(reviews)
        assert result.approved is True
        assert result.feedback is None

    def test_any_rejection_rejects(self):
        reviews = [
            ("claude", ReviewVerdict(approved=True, feedback=None)),
            ("gemini", ReviewVerdict(approved=False, feedback="Style issues")),
        ]
        result = synthesize_reviews(reviews)
        assert result.approved is False
        assert "gemini" in result.feedback.lower()
        assert "Style issues" in result.feedback

    def test_multiple_rejections_aggregate(self):
        reviews = [
            ("claude", ReviewVerdict(approved=False, feedback="Bug in line 42")),
            ("gemini", ReviewVerdict(approved=False, feedback="Missing tests")),
            ("codex", ReviewVerdict(approved=True, feedback=None)),
        ]
        result = synthesize_reviews(reviews)
        assert result.approved is False
        assert "Bug in line 42" in result.feedback
        assert "Missing tests" in result.feedback

    def test_single_reviewer(self):
        reviews = [
            ("claude", ReviewVerdict(approved=False, feedback="Needs work")),
        ]
        result = synthesize_reviews(reviews)
        assert result.approved is False

    def test_empty_reviews(self):
        result = synthesize_reviews([])
        assert result.approved is False
