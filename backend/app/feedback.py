"""Owner-scoped feedback with an allowlisted, text-free diagnostic snapshot.

No request, venue, coordinates, headers, tool arguments or model prose are stored.
User reports are triage labels, not automatically trusted regression expectations.
"""

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.results import PlanResult

FailureCategory = Literal[
    "model_error", "tool_error", "stale_data", "validator_miss", "client_error", "other"
]
RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_RUNS_PER_USER = 100


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    day_index: int | None = Field(default=None, ge=0)
    activity_index: int | None = Field(default=None, ge=0)
    helpful: bool
    category: FailureCategory = "other"

    @model_validator(mode="after")
    def paired_target(self) -> "FeedbackRequest":
        if (self.day_index is None) != (self.activity_index is None):
            raise ValueError("day_index and activity_index must be supplied together")
        return self


def diagnostic_snapshot(result: PlanResult) -> dict:
    """A structural trace that cannot retain private text through a new schema field."""
    plan = result.itinerary
    return {
        "schema_version": 1,
        "days": [
            [
                {
                    "start_time": a.start_time,
                    "end_time": a.end_time,
                    "estimated_cost": a.estimated_cost,
                    "category": a.category,
                    "travel_mode": a.travel_mode,
                }
                for a in day.activities
            ]
            for day in plan.days
        ]
        if plan
        else [],
        "budget": result.constraints.budget,
        "total_estimated_cost": plan.total_estimated_cost if plan else None,
        "violations": [v.code for v in result.validation.violations] if result.validation else [],
        "tool_failures": sum(not call.ok for call in result.tool_calls),
    }


class FeedbackStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, created REAL NOT NULL,
                snapshot TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS runs_owner ON runs(owner, created);
            CREATE TABLE IF NOT EXISTS feedback (
                run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                target TEXT NOT NULL, helpful INTEGER NOT NULL, category TEXT NOT NULL,
                PRIMARY KEY(run_id, target)
            );
        """)
        return connection

    async def record(self, owner: str, result: PlanResult) -> None:
        if not owner:
            return
        snapshot = diagnostic_snapshot(result)

        def write() -> None:
            connection = self._connect()
            try:
                with connection:
                    connection.execute(
                        "DELETE FROM runs WHERE created < ?", (time.time() - RETENTION_SECONDS,)
                    )
                    connection.execute(
                        "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?)",
                        (result.run_id, owner, time.time(), json.dumps(snapshot)),
                    )
                    connection.execute(
                        "DELETE FROM runs WHERE owner = ? AND id NOT IN "
                        "(SELECT id FROM runs WHERE owner = ? ORDER BY created DESC LIMIT ?)",
                        (owner, owner, MAX_RUNS_PER_USER),
                    )
            finally:
                connection.close()

        await asyncio.to_thread(write)

    async def prune(self) -> int:
        """Delete expired snapshots and their cascading feedback rows."""

        def write() -> int:
            connection = self._connect()
            try:
                with connection:
                    cursor = connection.execute(
                        "DELETE FROM runs WHERE created < ?",
                        (time.time() - RETENTION_SECONDS,),
                    )
                return cursor.rowcount
            finally:
                connection.close()

        return await asyncio.to_thread(write)

    async def submit(self, owner: str, payload: FeedbackRequest) -> bool:
        def write() -> bool:
            connection = self._connect()
            try:
                with connection:
                    connection.execute(
                        "DELETE FROM runs WHERE created < ?",
                        (time.time() - RETENTION_SECONDS,),
                    )
                    row = connection.execute(
                        "SELECT snapshot FROM runs WHERE id = ? AND owner = ? AND created >= ?",
                        (payload.run_id, owner, time.time() - RETENTION_SECONDS),
                    ).fetchone()
                    if row is None:
                        return False
                    if payload.day_index is not None:
                        days = json.loads(row[0])["days"]
                        if payload.day_index >= len(days) or payload.activity_index >= len(
                            days[payload.day_index]
                        ):
                            return False
                    target = (
                        "plan"
                        if payload.day_index is None
                        else f"{payload.day_index}:{payload.activity_index}"
                    )
                    connection.execute(
                        "INSERT OR REPLACE INTO feedback VALUES (?, ?, ?, ?)",
                        (payload.run_id, target, payload.helpful, payload.category),
                    )
                return True
            finally:
                connection.close()

        return await asyncio.to_thread(write)

    async def export(self) -> list[dict]:
        """Local operator access only. No identity or run ID leaves this boundary."""

        def read() -> list[dict]:
            connection = self._connect()
            try:
                with connection:
                    connection.execute(
                        "DELETE FROM runs WHERE created < ?",
                        (time.time() - RETENTION_SECONDS,),
                    )
                    rows = connection.execute(
                        "SELECT r.snapshot, f.target, f.helpful, f.category FROM feedback f "
                        "JOIN runs r ON f.run_id = r.id"
                    ).fetchall()
                return [
                    {
                        "snapshot": json.loads(s),
                        "target": t,
                        "helpful": bool(h),
                        "category": c,
                        "review_status": "unreviewed",
                    }
                    for s, t, h, c in rows
                ]
            finally:
                connection.close()

        return await asyncio.to_thread(read)
