"""Tests for PR comments extraction."""

from auto_dev_loop.comments import parse_review_comments, filter_actionable, format_for_agent


SAMPLE_COMMENTS = [
    {"author": {"login": "reviewer1"}, "body": "Needs error handling", "path": "src/auth.py", "line": 42, "state": "PENDING"},
    {"author": {"login": "reviewer2"}, "body": "LGTM", "path": None, "line": None, "state": "APPROVED"},
]


def test_parse_review_comments():
    comments = parse_review_comments(SAMPLE_COMMENTS)
    assert len(comments) == 2
    assert comments[0]["author"] == "reviewer1"


def test_filter_actionable():
    actionable = filter_actionable(parse_review_comments(SAMPLE_COMMENTS))
    assert len(actionable) == 1
    assert actionable[0]["path"] == "src/auth.py"


def test_format_for_agent():
    comments = parse_review_comments(SAMPLE_COMMENTS[:1])
    text = format_for_agent(comments)
    assert "src/auth.py:42" in text
    assert "error handling" in text
