"""Tests for PR status checking."""

from auto_dev_loop.pr_status import parse_pr_status, PrStatus


APPROVED_PR = {
    "state": "OPEN",
    "mergeable": "MERGEABLE",
    "reviewDecision": "APPROVED",
    "statusCheckRollup": [{"state": "SUCCESS", "context": "CI"}],
}

CHANGES_REQUESTED_PR = {
    "state": "OPEN",
    "mergeable": "MERGEABLE",
    "reviewDecision": "CHANGES_REQUESTED",
    "statusCheckRollup": [{"state": "SUCCESS", "context": "CI"}],
}

PENDING_CI_PR = {
    "state": "OPEN",
    "mergeable": "MERGEABLE",
    "reviewDecision": "APPROVED",
    "statusCheckRollup": [{"state": "PENDING", "context": "CI"}],
}


def test_approved_pr():
    status = parse_pr_status(APPROVED_PR)
    assert status.review_approved is True
    assert status.ci_passing is True
    assert status.ready_to_merge is True


def test_changes_requested():
    status = parse_pr_status(CHANGES_REQUESTED_PR)
    assert status.review_approved is False
    assert status.ready_to_merge is False


def test_pending_ci():
    status = parse_pr_status(PENDING_CI_PR)
    assert status.ci_passing is False
    assert status.ready_to_merge is False


def test_no_checks():
    data = {"state": "OPEN", "mergeable": "MERGEABLE", "reviewDecision": "APPROVED", "statusCheckRollup": []}
    status = parse_pr_status(data)
    assert status.ci_passing is True
