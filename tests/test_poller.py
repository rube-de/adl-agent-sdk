"""Tests for GitHub Projects V2 poller."""

import pytest

from auto_dev_loop.poller import parse_project_items, PollError
from auto_dev_loop.models import Issue


SAMPLE_GH_OUTPUT = {
    "items": {
        "nodes": [
            {
                "id": "item_1",
                "content": {
                    "__typename": "Issue",
                    "number": 42,
                    "title": "Fix auth bug",
                    "body": "The login page crashes",
                    "labels": {"nodes": [{"name": "bug"}]},
                    "repository": {"nameWithOwner": "owner/repo"},
                },
                "fieldValueByName": {"name": "Ready for Dev"},
            },
            {
                "id": "item_2",
                "content": {
                    "__typename": "Issue",
                    "number": 43,
                    "title": "Add docs",
                    "body": "Need API docs",
                    "labels": {"nodes": [{"name": "docs"}]},
                    "repository": {"nameWithOwner": "owner/repo"},
                },
                "fieldValueByName": {"name": "In Progress"},
            },
        ]
    }
}


def test_parse_project_items_filters_by_column():
    issues = parse_project_items(SAMPLE_GH_OUTPUT, "Ready for Dev")
    assert len(issues) == 1
    assert issues[0].number == 42
    assert issues[0].title == "Fix auth bug"
    assert issues[0].labels == ["bug"]
    assert issues[0].project_item_id == "item_1"


def test_parse_project_items_empty():
    issues = parse_project_items({"items": {"nodes": []}}, "Ready for Dev")
    assert issues == []


def test_parse_project_items_skips_pull_requests():
    data = {
        "items": {
            "nodes": [{
                "id": "item_3",
                "content": {"__typename": "PullRequest"},
                "fieldValueByName": {"name": "Ready for Dev"},
            }]
        }
    }
    issues = parse_project_items(data, "Ready for Dev")
    assert issues == []


def test_parse_project_items_handles_null_content():
    data = {
        "items": {
            "nodes": [{
                "id": "item_4",
                "content": None,
                "fieldValueByName": {"name": "Ready for Dev"},
            }]
        }
    }
    issues = parse_project_items(data, "Ready for Dev")
    assert issues == []
