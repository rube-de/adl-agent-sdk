"""Tests for SQLite state store."""

import pytest

from auto_dev_loop.state import StateStore


@pytest.fixture
async def db(tmp_path):
    store = StateStore(tmp_path / "test.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_init_creates_tables(db: StateStore):
    tables = await db.list_tables()
    assert "issues" in tables
    assert "review_iterations" in tables
    assert "workflow_stages" in tables


@pytest.mark.asyncio
async def test_upsert_issue(db: StateStore):
    await db.upsert_issue(
        repo="owner/repo", number=42, title="Fix bug",
        state="DETECTED", project_item_id="item_1",
    )
    issue = await db.get_issue("owner/repo", 42)
    assert issue is not None
    assert issue["title"] == "Fix bug"
    assert issue["state"] == "DETECTED"


@pytest.mark.asyncio
async def test_update_state(db: StateStore):
    await db.upsert_issue(repo="o/r", number=1, title="t", state="DETECTED")
    issue = await db.get_issue("o/r", 1)
    await db.update_state(issue["id"], "PLANNING")
    issue = await db.get_issue("o/r", 1)
    assert issue["state"] == "PLANNING"


@pytest.mark.asyncio
async def test_list_active_issues(db: StateStore):
    await db.upsert_issue(repo="o/r", number=1, title="a", state="PLANNING")
    await db.upsert_issue(repo="o/r", number=2, title="b", state="DONE")
    await db.upsert_issue(repo="o/r", number=3, title="c", state="DEV_CYCLE_1")
    active = await db.list_active_issues()
    numbers = [i["number"] for i in active]
    assert 1 in numbers
    assert 3 in numbers
    assert 2 not in numbers


@pytest.mark.asyncio
async def test_store_review_iteration(db: StateStore):
    await db.upsert_issue(repo="o/r", number=1, title="t", state="REVIEW")
    issue = await db.get_issue("o/r", 1)
    await db.store_review_iteration(
        issue_id=issue["id"], dev_cycle=1, iteration=1,
        worker_output="diff", reviewer_output="APPROVED",
        approved=True, reviewer_models=["claude"],
    )
    iters = await db.get_review_iterations(issue["id"])
    assert len(iters) == 1
    assert iters[0]["approved"] == 1


@pytest.mark.asyncio
async def test_store_workflow_stage(db: StateStore):
    await db.upsert_issue(repo="o/r", number=1, title="t", state="STAGE_PLAN")
    issue = await db.get_issue("o/r", 1)
    await db.store_workflow_stage(
        issue_id=issue["id"], workflow_id="bug_fix",
        stage_ref="plan", stage_index=0, iteration=1,
        status="approved", verdict="approved",
    )
    stages = await db.get_workflow_stages(issue["id"])
    assert len(stages) == 1
    assert stages[0]["stage_ref"] == "plan"


@pytest.mark.asyncio
async def test_upsert_is_idempotent(db: StateStore):
    await db.upsert_issue(repo="o/r", number=1, title="t", state="DETECTED")
    await db.upsert_issue(repo="o/r", number=1, title="t2", state="PLANNING")
    issue = await db.get_issue("o/r", 1)
    assert issue["title"] == "t2"
    assert issue["state"] == "PLANNING"


@pytest.mark.asyncio
async def test_list_terminal_issue_keys(db: StateStore):
    await db.upsert_issue(repo="o/r", number=1, title="a", state="completed")
    await db.upsert_issue(repo="o/r", number=2, title="b", state="failed")
    await db.upsert_issue(repo="o/r", number=3, title="c", state="planning")
    await db.upsert_issue(repo="o/r", number=4, title="d", state="escalated")

    keys = await db.list_terminal_issue_keys()

    assert keys == {"o/r#1", "o/r#2", "o/r#4"}
    assert "o/r#3" not in keys


@pytest.mark.asyncio
async def test_list_terminal_issue_keys_empty(db: StateStore):
    keys = await db.list_terminal_issue_keys()
    assert keys == set()
