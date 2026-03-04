"""Tests for GitHub Projects V2 poller."""

import asyncio

import pytest

import auto_dev_loop.poller as _poller_mod
from auto_dev_loop.poller import parse_project_items, PollError
from auto_dev_loop.models import Issue


SAMPLE_NODES = [
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

# Used in poll tests as a fake GraphQL projectV2 response body.
SAMPLE_GH_OUTPUT = {"items": {"nodes": SAMPLE_NODES}}


def test_parse_project_items_filters_by_column():
    issues = parse_project_items(SAMPLE_NODES, "Ready for Dev")
    assert len(issues) == 1
    assert issues[0].id == 100042
    assert issues[0].number == 42
    assert issues[0].title == "Fix auth bug"
    assert issues[0].labels == ["bug"]
    assert issues[0].project_item_id == "item_1"


def test_parse_project_items_unique_ids():
    """Each issue gets its globally unique databaseId, not a hardcoded 0."""
    issues = parse_project_items(SAMPLE_NODES, "Ready for Dev")
    in_progress = parse_project_items(SAMPLE_NODES, "In Progress")
    all_issues = issues + in_progress
    ids = [i.id for i in all_issues]
    assert ids == [100042, 100043]
    assert len(set(ids)) == len(ids), "IDs must be unique"


def test_parse_project_items_empty():
    issues = parse_project_items([], "Ready for Dev")
    assert issues == []


def test_parse_project_items_skips_pull_requests():
    nodes = [{
        "id": "item_3",
        "content": {"__typename": "PullRequest"},
        "fieldValueByName": {"name": "Ready for Dev"},
    }]
    issues = parse_project_items(nodes, "Ready for Dev")
    assert issues == []


def test_parse_project_items_handles_null_content():
    nodes = [{
        "id": "item_4",
        "content": None,
        "fieldValueByName": {"name": "Ready for Dev"},
    }]
    issues = parse_project_items(nodes, "Ready for Dev")
    assert issues == []


# ---------------------------------------------------------------------------
# Tests for poll_project_issues auto-detect (user vs org)
# ---------------------------------------------------------------------------

async def test_poll_uses_user_query_when_user_project_exists(monkeypatch):
    """User query returns data: result is used and owner type cached as 'user'."""
    user_response = {"data": {"user": {"projectV2": SAMPLE_GH_OUTPUT}}}

    async def fake_run_query(query, owner, project_number, *, cursor=None):
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

    async def fake_run_query(query, owner, project_number, *, cursor=None):
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

    async def fake_run_query(query, owner, project_number, *, cursor=None):
        call_log.append(query)
        return {"data": {"organization": {"projectV2": SAMPLE_GH_OUTPUT}}}

    monkeypatch.setattr(_poller_mod, "_run_query", fake_run_query)
    monkeypatch.setattr(_poller_mod, "_owner_type_cache", {("cachedorg", 3): "org"})

    issues = await _poller_mod.poll_project_issues("cachedorg", 3, "Ready for Dev")

    assert len(issues) == 1
    assert len(call_log) == 1
    assert call_log[0] == _poller_mod.ORG_PROJECT_ITEMS_QUERY


def test_items_fragment_includes_page_info():
    """Query strings must declare cursor variable and request pageInfo."""
    import auto_dev_loop.poller as m
    for query_str in (m.USER_PROJECT_ITEMS_QUERY, m.ORG_PROJECT_ITEMS_QUERY):
        assert "$cursor: String" in query_str
        assert "after: $cursor" in query_str
        assert "pageInfo" in query_str
        assert "hasNextPage" in query_str
        assert "endCursor" in query_str


async def test_poll_returns_empty_when_neither_user_nor_org_has_project(monkeypatch):
    """Both user and org return null: returns empty list."""
    async def fake_run_query(query, owner, project_number, *, cursor=None):
        if query == _poller_mod.USER_PROJECT_ITEMS_QUERY:
            return {"data": {"user": None}}
        return {"data": {"organization": None}}

    monkeypatch.setattr(_poller_mod, "_run_query", fake_run_query)
    monkeypatch.setattr(_poller_mod, "_owner_type_cache", {})

    issues = await _poller_mod.poll_project_issues("nobody", 99, "Ready for Dev")

    assert issues == []


async def test_run_query_passes_cursor_when_provided(monkeypatch):
    """_run_query should pass -f cursor=<value> to gh when cursor is not None."""
    captured_args = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_args.extend(args)

        class FakeProc:
            returncode = 0
            async def communicate(self):
                return (b'{"data": {}}', b"")

        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    await _poller_mod._run_query("query {}", "owner", 1, cursor="abc123")
    assert "cursor=abc123" in captured_args


async def test_run_query_omits_cursor_when_none(monkeypatch):
    """_run_query should NOT pass any cursor arg when cursor is None."""
    captured_args = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_args.extend(args)

        class FakeProc:
            returncode = 0
            async def communicate(self):
                return (b'{"data": {}}', b"")

        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    await _poller_mod._run_query("query {}", "owner", 1, cursor=None)
    assert not any("cursor" in str(a) for a in captured_args)


# ---------------------------------------------------------------------------
# Tests for _fetch_all_project_items_nodes pagination helper
# ---------------------------------------------------------------------------

async def test_fetch_all_items_returns_none_when_project_not_found(monkeypatch):
    """Returns None when projectV2 is null (wrong owner type)."""
    async def fake_run_query(query, owner, number, *, cursor=None):
        return {"data": {"user": None}}

    monkeypatch.setattr(_poller_mod, "_run_query", fake_run_query)
    result = await _poller_mod._fetch_all_project_items_nodes(
        _poller_mod.USER_PROJECT_ITEMS_QUERY, "user", "myuser", 1
    )
    assert result is None


async def test_fetch_all_items_returns_empty_list_for_empty_project(monkeypatch):
    """Returns [] when project exists but has no items."""
    async def fake_run_query(query, owner, number, *, cursor=None):
        return {"data": {"user": {"projectV2": {"items": {
            "nodes": [],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }}}}}

    monkeypatch.setattr(_poller_mod, "_run_query", fake_run_query)
    result = await _poller_mod._fetch_all_project_items_nodes(
        _poller_mod.USER_PROJECT_ITEMS_QUERY, "user", "myuser", 1
    )
    assert result == []


async def test_fetch_all_items_follows_pagination(monkeypatch):
    """Fetches multiple pages and returns all nodes combined."""
    page1_node = {"id": "item_p1", "content": {"__typename": "Issue"}, "fieldValueByName": None}
    page2_node = {"id": "item_p2", "content": {"__typename": "Issue"}, "fieldValueByName": None}
    call_cursors = []

    async def fake_run_query(query, owner, number, *, cursor=None):
        call_cursors.append(cursor)
        if cursor is None:
            return {"data": {"user": {"projectV2": {"items": {
                "nodes": [page1_node],
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor_abc"},
            }}}}}
        return {"data": {"user": {"projectV2": {"items": {
            "nodes": [page2_node],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }}}}}

    monkeypatch.setattr(_poller_mod, "_run_query", fake_run_query)
    result = await _poller_mod._fetch_all_project_items_nodes(
        _poller_mod.USER_PROJECT_ITEMS_QUERY, "user", "myuser", 1
    )
    assert result == [page1_node, page2_node]
    assert call_cursors == [None, "cursor_abc"]


async def test_fetch_all_items_raises_on_mid_pagination_failure(monkeypatch):
    """Raises PollError if project_data disappears after first successful page."""
    call_count = 0

    async def fake_run_query(query, owner, number, *, cursor=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"data": {"user": {"projectV2": {"items": {
                "nodes": [{"id": "item_1"}],
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor_x"},
            }}}}}
        # Second page: project vanishes (transient API error / data race)
        return {"data": {"user": None}}

    monkeypatch.setattr(_poller_mod, "_run_query", fake_run_query)
    with pytest.raises(_poller_mod.PollError, match="mid-pagination"):
        await _poller_mod._fetch_all_project_items_nodes(
            _poller_mod.USER_PROJECT_ITEMS_QUERY, "user", "myuser", 1
        )


async def test_fetch_all_items_warns_when_max_pages_reached(monkeypatch, caplog):
    """Logs a warning and returns partial results when _MAX_PAGES is exhausted."""
    import logging

    async def fake_run_query(query, owner, number, *, cursor=None):
        return {"data": {"user": {"projectV2": {"items": {
            "nodes": [{"id": f"item_{cursor}"}],
            "pageInfo": {"hasNextPage": True, "endCursor": f"cursor_{cursor or 0}"},
        }}}}}

    monkeypatch.setattr(_poller_mod, "_run_query", fake_run_query)
    monkeypatch.setattr(_poller_mod, "_MAX_PAGES", 3)  # use small limit for test speed

    with caplog.at_level(logging.WARNING, logger="auto_dev_loop.poller"):
        result = await _poller_mod._fetch_all_project_items_nodes(
            _poller_mod.USER_PROJECT_ITEMS_QUERY, "user", "myuser", 1
        )

    assert len(result) == 3  # 3 pages × 1 node each
    assert any("Pagination limit" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Integration test: poll_project_issues uses _fetch_all_project_items_nodes
# ---------------------------------------------------------------------------

async def test_poll_returns_issues_from_all_pages(monkeypatch):
    """poll_project_issues collects issues across pagination boundaries."""
    page1_item = {
        "id": "item_p1",
        "content": {
            "__typename": "Issue",
            "databaseId": 201,
            "number": 201,
            "title": "Page 1 Issue",
            "body": "",
            "labels": {"nodes": []},
            "repository": {"nameWithOwner": "o/r"},
        },
        "fieldValueByName": {"name": "Ready for Dev"},
    }
    page2_item = {
        "id": "item_p2",
        "content": {
            "__typename": "Issue",
            "databaseId": 202,
            "number": 202,
            "title": "Page 2 Issue",
            "body": "",
            "labels": {"nodes": []},
            "repository": {"nameWithOwner": "o/r"},
        },
        "fieldValueByName": {"name": "Ready for Dev"},
    }
    call_count = 0

    async def fake_run_query(query, owner, number, *, cursor=None):
        nonlocal call_count
        call_count += 1
        if cursor is None:
            return {"data": {"user": {"projectV2": {"items": {
                "nodes": [page1_item],
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor_p2"},
            }}}}}
        return {"data": {"user": {"projectV2": {"items": {
            "nodes": [page2_item],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }}}}}

    monkeypatch.setattr(_poller_mod, "_run_query", fake_run_query)
    monkeypatch.setattr(_poller_mod, "_owner_type_cache", {})

    issues = await _poller_mod.poll_project_issues("myuser", 1, "Ready for Dev")

    assert len(issues) == 2
    assert issues[0].number == 201
    assert issues[1].number == 202
    assert call_count == 2
