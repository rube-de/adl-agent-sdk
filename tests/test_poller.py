"""Tests for GitHub Projects V2 poller."""

import pytest

import auto_dev_loop.poller as _poller_mod
from auto_dev_loop.poller import parse_project_items, PollError
from auto_dev_loop.models import Issue


SAMPLE_GH_OUTPUT = {
    "items": {
        "nodes": [
            {
                "id": "item_1",
                "content": {
                    "__typename": "Issue",
                    "databaseId": 100042,
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
                    "databaseId": 100043,
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
    assert issues[0].id == 100042
    assert issues[0].number == 42
    assert issues[0].title == "Fix auth bug"
    assert issues[0].labels == ["bug"]
    assert issues[0].project_item_id == "item_1"


def test_parse_project_items_unique_ids():
    """Each issue gets its globally unique databaseId, not a hardcoded 0."""
    issues = parse_project_items(SAMPLE_GH_OUTPUT, "Ready for Dev")
    in_progress = parse_project_items(SAMPLE_GH_OUTPUT, "In Progress")
    all_issues = issues + in_progress
    ids = [i.id for i in all_issues]
    assert ids == [100042, 100043]
    assert len(set(ids)) == len(ids), "IDs must be unique"


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


# ---------------------------------------------------------------------------
# Tests for poll_project_issues auto-detect (user vs org)
# ---------------------------------------------------------------------------

async def test_poll_uses_user_query_when_user_project_exists(monkeypatch):
    """User query returns data: result is used and owner type cached as 'user'."""
    user_response = {"data": {"user": {"projectV2": SAMPLE_GH_OUTPUT}}}

    async def fake_run_query(query, owner, project_number):
        return user_response

    monkeypatch.setattr(_poller_mod, "_run_query", fake_run_query)
    monkeypatch.setattr(_poller_mod, "_owner_type_cache", {})

    issues = await _poller_mod.poll_project_issues("myuser", 1, "Ready for Dev")

    assert len(issues) == 1
    assert issues[0].number == 42
    assert _poller_mod._owner_type_cache[("myuser", 1)] == "user"


async def test_poll_falls_back_to_org_when_user_returns_null(monkeypatch):
    """User query returns null user: org query is tried and cached as 'org'."""
    call_log = []

    async def fake_run_query(query, owner, project_number):
        call_log.append(query)
        if query == _poller_mod.USER_PROJECT_ITEMS_QUERY:
            return {"data": {"user": None}}
        return {"data": {"organization": {"projectV2": SAMPLE_GH_OUTPUT}}}

    monkeypatch.setattr(_poller_mod, "_run_query", fake_run_query)
    monkeypatch.setattr(_poller_mod, "_owner_type_cache", {})

    issues = await _poller_mod.poll_project_issues("myorg", 2, "Ready for Dev")

    assert len(issues) == 1
    assert issues[0].number == 42
    assert _poller_mod._owner_type_cache[("myorg", 2)] == "org"
    assert len(call_log) == 2  # both queries were tried


async def test_poll_uses_cached_org_type_without_user_query(monkeypatch):
    """When cache already says 'org', only org query is run (no user query)."""
    call_log = []

    async def fake_run_query(query, owner, project_number):
        call_log.append(query)
        return {"data": {"organization": {"projectV2": SAMPLE_GH_OUTPUT}}}

    monkeypatch.setattr(_poller_mod, "_run_query", fake_run_query)
    monkeypatch.setattr(_poller_mod, "_owner_type_cache", {("cachedorg", 3): "org"})

    issues = await _poller_mod.poll_project_issues("cachedorg", 3, "Ready for Dev")

    assert len(issues) == 1
    assert len(call_log) == 1
    assert call_log[0] == _poller_mod.ORG_PROJECT_ITEMS_QUERY


async def test_poll_returns_empty_when_neither_user_nor_org_has_project(monkeypatch):
    """Both user and org return null: returns empty list."""
    async def fake_run_query(query, owner, project_number):
        if query == _poller_mod.USER_PROJECT_ITEMS_QUERY:
            return {"data": {"user": None}}
        return {"data": {"organization": None}}

    monkeypatch.setattr(_poller_mod, "_run_query", fake_run_query)
    monkeypatch.setattr(_poller_mod, "_owner_type_cache", {})

    issues = await _poller_mod.poll_project_issues("nobody", 99, "Ready for Dev")

    assert issues == []
