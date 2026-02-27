"""Tests for structured review verdict parsing."""

import pytest

from auto_dev_loop.review_parser import (
    parse_review_verdict,
    synthesize_reviews,
)
from auto_dev_loop.models import ReviewVerdict


class TestParseReviewVerdict:

    def test_approved_simple(self):
        v = parse_review_verdict("Everything looks good.\n\nAPPROVED")
        assert v.approved is True
        assert v.feedback is None

    def test_approved_with_trailing_whitespace(self):
        v = parse_review_verdict("Good.\n\nAPPROVED\n  \n")
        assert v.approved is True

    def test_needs_revision_with_feedback(self):
        output = """\
The code has issues.

## Feedback

1. Missing error handling in auth.py
2. No tests for edge case

NEEDS_REVISION"""
        v = parse_review_verdict(output)
        assert v.approved is False
        assert "Missing error handling" in v.feedback
        assert "No tests" in v.feedback

    def test_needs_revision_without_feedback_section(self):
        output = "This code is bad.\n\nNEEDS_REVISION"
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
        output = "Status: APPROVED\n\nAPPROVED"
        v = parse_review_verdict(output)
        assert v.approved is True

    def test_vetoed(self):
        output = "Security risk.\n\nVETOED"
        v = parse_review_verdict(output)
        assert v.approved is False
        assert v.feedback == output


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
