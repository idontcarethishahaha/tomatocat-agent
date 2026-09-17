"""Small persistent task state store shared by background runtimes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_STATES = {"created", "queued", "running", "completed", "partial", "failed", "cancelled", "retry_wait"}
RETRY_POLICIES = {"safe", "confirm", "never"}


@dataclass(frozen=True)
class TaskRecord:
    job_id: str
    task_type: str
    status: str
    payload: str = ""
    result: str = ""
    error: str = ""
    retry_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    max_retries: int = 3
    result_path: str = ""
    delivery_status: str = "not_required"
    delivery_error: str = ""
    delivery_message: str = ""
    delivery_attempts: int = 0
    label: str = ""
    profile: str = ""
    origin_channel: str = ""
    origin_chat_id: str = ""
    retry_policy: str = "confirm"


class TaskStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                    job_id TEXT PRIMARY KEY, task_type TEXT NOT NULL, status TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '', retry_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT '', max_retries INTEGER NOT NULL DEFAULT 3,
                    result_path TEXT NOT NULL DEFAULT ''
                )"""
            )
            columns = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
            for name, definition in (
                ("finished_at", "TEXT NOT NULL DEFAULT ''"),
                ("max_retries", "INTEGER NOT NULL DEFAULT 3"),
                ("result_path", "TEXT NOT NULL DEFAULT ''"),
                ("delivery_status", "TEXT NOT NULL DEFAULT 'not_required'"),
                ("delivery_error", "TEXT NOT NULL DEFAULT ''"),
                ("delivery_message", "TEXT NOT NULL DEFAULT ''"),
                ("delivery_attempts", "INTEGER NOT NULL DEFAULT 0"),
                ("label", "TEXT NOT NULL DEFAULT ''"),
                ("profile", "TEXT NOT NULL DEFAULT ''"),
                ("origin_channel", "TEXT NOT NULL DEFAULT ''"),
                ("origin_chat_id", "TEXT NOT NULL DEFAULT ''"),
                ("retry_policy", "TEXT NOT NULL DEFAULT 'confirm'"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, job_id: str, task_type: str, *, payload: str = "", status: str = "created", retry_count: int = 0, max_retries: int = 3, result_path: str = "", label: str = "", profile: str = "", origin_channel: str = "", origin_chat_id: str = "", retry_policy: str = "confirm") -> TaskRecord:
        if status not in TASK_STATES:
            raise ValueError(f"Unknown task status: {status}")
        if retry_policy not in RETRY_POLICIES:
            raise ValueError(f"Unknown retry policy: {retry_policy}")
        now = self._now()
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO tasks(job_id,task_type,status,payload,retry_count,created_at,updated_at,max_retries,result_path,label,profile,origin_channel,origin_chat_id,retry_policy) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, task_type, status, payload, retry_count, now, now, max_retries, result_path, label, profile, origin_channel, origin_chat_id, retry_policy),
            )
        return self.get(job_id)  # type: ignore[return-value]

    def update(self, job_id: str, status: str, *, result: str | None = None, error: str | None = None, expected_status: str | None = None) -> bool:
        if status not in TASK_STATES:
            raise ValueError(f"Unknown task status: {status}")
        fields = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status, self._now()]
        if result is not None:
            fields.append("result = ?"); values.append(result)
        if error is not None:
            fields.append("error = ?"); values.append(error)
        if status in {"completed", "partial", "failed", "cancelled"}:
            fields.append("finished_at = ?"); values.append(self._now())
        values.append(job_id)
        sql = f"UPDATE tasks SET {', '.join(fields)} WHERE job_id = ?"
        if expected_status is not None:
            sql += " AND status = ?"; values.append(expected_status)
        with sqlite3.connect(self.path) as conn:
            return conn.execute(sql, values).rowcount == 1

    def get(self, job_id: str) -> TaskRecord | None:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM tasks WHERE job_id = ?", (job_id,)).fetchone()
        return TaskRecord(**dict(row)) if row else None

    def list_unfinished(self) -> list[TaskRecord]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM tasks WHERE status IN ('created','queued','running','retry_wait') ORDER BY created_at").fetchall()
        return [TaskRecord(**dict(row)) for row in rows]

    def list_all(self) -> list[TaskRecord]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [TaskRecord(**dict(row)) for row in rows]

    def update_delivery(self, job_id: str, status: str, *, error: str = "", expected_status: str | None = None, increment_attempts: bool = False) -> bool:
        if status not in {"not_required", "pending", "sending", "delivered", "failed"}:
            raise ValueError(f"Unknown delivery status: {status}")
        attempts_sql = ", delivery_attempts=delivery_attempts+1" if increment_attempts else ""
        sql = f"UPDATE tasks SET delivery_status=?, delivery_error=?, updated_at=?{attempts_sql} WHERE job_id=?"
        values: list[Any] = [status, error, self._now(), job_id]
        if expected_status is not None:
            sql += " AND delivery_status=?"
            values.append(expected_status)
        with sqlite3.connect(self.path) as conn:
            return conn.execute(sql, values).rowcount == 1

    def finish_and_queue_delivery(
        self,
        job_id: str,
        status: str,
        *,
        result: str,
        error: str = "",
        delivery_message: str = "",
    ) -> bool:
        """Atomically persist the terminal result and its unsent notification."""
        if status not in {"completed", "partial", "failed", "cancelled"}:
            raise ValueError(f"Invalid terminal status: {status}")
        now = self._now()
        delivery_status = "pending" if delivery_message else "not_required"
        with sqlite3.connect(self.path) as conn:
            return conn.execute(
                """UPDATE tasks SET status=?, result=?, error=?, finished_at=?, updated_at=?,
                   delivery_status=?, delivery_error='', delivery_message=?
                   WHERE job_id=? AND status IN ('created','queued','running','retry_wait')""",
                (status, result, error, now, now, delivery_status, delivery_message, job_id),
            ).rowcount == 1

    def claim_delivery(self, job_id: str) -> TaskRecord | None:
        """Claim a never-attempted notification without replaying uncertain sends."""
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            if conn.execute(
                "UPDATE tasks SET delivery_status='sending', updated_at=? WHERE job_id=? AND delivery_status='pending'",
                (self._now(), job_id),
            ).rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM tasks WHERE job_id=?", (job_id,)).fetchone()
        return TaskRecord(**dict(row)) if row else None

    def list_pending_deliveries(self) -> list[TaskRecord]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM tasks WHERE delivery_status='pending' ORDER BY updated_at"
            ).fetchall()
        return [TaskRecord(**dict(row)) for row in rows]

    def claim_retry(self, job_id: str, *, allow_side_effects: bool = False) -> TaskRecord | None:
        """Atomically move one interrupted subagent into queued state."""
        now = self._now()
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM tasks WHERE job_id=?", (job_id,)).fetchone()
            if not row or row["task_type"] != "subagent" or row["status"] != "retry_wait":
                return None
            if row["retry_count"] >= row["max_retries"]:
                return None
            if row["retry_policy"] == "never":
                return None
            if row["retry_policy"] != "safe" and not allow_side_effects:
                return None
            if conn.execute("UPDATE tasks SET status='queued', retry_count=retry_count+1, error='', updated_at=? WHERE job_id=? AND status='retry_wait' AND retry_count < max_retries", (now, job_id)).rowcount != 1:
                return None
            result = conn.execute("SELECT * FROM tasks WHERE job_id=?", (job_id,)).fetchone()
        return TaskRecord(**dict(result)) if result else None

    def mark_interrupted(self) -> int:
        """Classify interrupted jobs conservatively after a process restart.

        Subagent payloads contain enough metadata for a future explicit retry;
        scheduler executions do not, so they are terminal failures instead of
        being advertised as retryable.
        """
        now = self._now()
        with sqlite3.connect(self.path) as conn:
            cur = conn.execute(
                "UPDATE tasks SET status='retry_wait', error='process interrupted; awaiting explicit retry', updated_at=? WHERE task_type='subagent' AND status IN ('created','queued','running')",
                (now,),
            )
            subagents = cur.rowcount
            conn.execute(
                "UPDATE tasks SET status='failed', error='process interrupted; scheduler execution is not automatically replayed', finished_at=?, updated_at=? WHERE task_type='scheduler' AND status IN ('created','queued','running')",
                (now, now),
            )
            return subagents
