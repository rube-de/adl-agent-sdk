"""SQLite state store for issue tracking and review history."""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from .models import TERMINAL_ISSUE_STATES


class StateStore:
    def __init__(self, db_path: Path):
        self._path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._create_tables()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def _create_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY,
                repo TEXT NOT NULL,
                number INTEGER NOT NULL,
                title TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'DETECTED',
                worktree_path TEXT,
                project_item_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(repo, number)
            );

            CREATE TABLE IF NOT EXISTS review_iterations (
                id INTEGER PRIMARY KEY,
                issue_id INTEGER REFERENCES issues(id),
                dev_cycle INTEGER NOT NULL,
                iteration INTEGER NOT NULL,
                worker_output_summary TEXT,
                reviewer_output_summary TEXT,
                approved BOOLEAN,
                reviewer_models TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(issue_id, dev_cycle, iteration)
            );

            CREATE TABLE IF NOT EXISTS workflow_stages (
                id INTEGER PRIMARY KEY,
                issue_id INTEGER REFERENCES issues(id),
                workflow_id TEXT NOT NULL,
                stage_ref TEXT NOT NULL,
                stage_index INTEGER NOT NULL,
                iteration INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending',
                agent_output_summary TEXT,
                verdict TEXT,
                feedback TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(issue_id, workflow_id, stage_ref, iteration)
            );
        """)
        await self._db.commit()

    async def list_tables(self) -> list[str]:
        cursor = await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        rows = await cursor.fetchall()
        return [r["name"] for r in rows]

    async def upsert_issue(
        self, repo: str, number: int, title: str, state: str,
        project_item_id: str | None = None,
    ) -> None:
        await self._db.execute("""
            INSERT INTO issues (repo, number, title, state, project_item_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(repo, number) DO UPDATE SET
                title=excluded.title,
                state=excluded.state,
                project_item_id=COALESCE(excluded.project_item_id, project_item_id),
                updated_at=CURRENT_TIMESTAMP
        """, (repo, number, title, state, project_item_id))
        await self._db.commit()

    async def get_issue(self, repo: str, number: int) -> dict | None:
        cursor = await self._db.execute(
            "SELECT * FROM issues WHERE repo=? AND number=?",
            (repo, number),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def update_state(self, issue_id: int, state: str) -> None:
        await self._db.execute(
            "UPDATE issues SET state=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (state, issue_id),
        )
        await self._db.commit()

    async def list_active_issues(self) -> list[dict]:
        placeholders = ", ".join("?" for _ in TERMINAL_ISSUE_STATES)
        cursor = await self._db.execute(
            f"SELECT * FROM issues WHERE state NOT IN ({placeholders})",
            tuple(TERMINAL_ISSUE_STATES),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def store_review_iteration(
        self, issue_id: int, dev_cycle: int, iteration: int,
        worker_output: str, reviewer_output: str,
        approved: bool, reviewer_models: list[str],
    ) -> None:
        await self._db.execute("""
            INSERT OR REPLACE INTO review_iterations
            (issue_id, dev_cycle, iteration, worker_output_summary,
             reviewer_output_summary, approved, reviewer_models)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            issue_id, dev_cycle, iteration,
            worker_output[:2000], reviewer_output[:2000],
            approved, json.dumps(reviewer_models),
        ))
        await self._db.commit()

    async def get_review_iterations(self, issue_id: int) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM review_iterations WHERE issue_id=? ORDER BY dev_cycle, iteration",
            (issue_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def store_workflow_stage(
        self, issue_id: int, workflow_id: str,
        stage_ref: str, stage_index: int, iteration: int,
        status: str, verdict: str | None = None,
        feedback: str | None = None,
        agent_output_summary: str | None = None,
    ) -> None:
        await self._db.execute("""
            INSERT OR REPLACE INTO workflow_stages
            (issue_id, workflow_id, stage_ref, stage_index, iteration,
             status, verdict, feedback, agent_output_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            issue_id, workflow_id, stage_ref, stage_index, iteration,
            status, verdict, feedback,
            agent_output_summary[:2000] if agent_output_summary else None,
        ))
        await self._db.commit()

    async def get_workflow_stages(self, issue_id: int) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM workflow_stages WHERE issue_id=? ORDER BY stage_index, iteration",
            (issue_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def list_terminal_issue_keys(self) -> set[str]:
        """Return ``repo#number`` keys for issues in terminal states."""
        placeholders = ", ".join("?" for _ in TERMINAL_ISSUE_STATES)
        cursor = await self._db.execute(
            f"SELECT repo, number FROM issues WHERE state IN ({placeholders})",
            tuple(TERMINAL_ISSUE_STATES),
        )
        rows = await cursor.fetchall()
        return {f"{r['repo']}#{r['number']}" for r in rows}
