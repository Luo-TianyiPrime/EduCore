"""Task, batch, event and analysis-cache persistence."""

from __future__ import annotations

import json
import uuid
from typing import Any

from backend.storage.db import Database, utc_now


class TaskRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- tasks -------------------------------------------------------------
    def create(
        self,
        *,
        mode: str,
        instruction: str,
        scope: dict[str, Any],
        budget_usd: float | None = None,
    ) -> dict[str, Any]:
        task_id = uuid.uuid4().hex[:16]
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tasks(
                    id, kind, mode, instruction, scope_json, status, progress,
                    stage, total_batches, done_batches, failed_batches, error,
                    result_path, checkpoint_path, budget_usd, created_at,
                    started_at, finished_at, updated_at
                ) VALUES (?, 'agent', ?, ?, ?, 'queued', 0, 'queued', 0, 0, 0,
                          NULL, NULL, NULL, ?, ?, NULL, NULL, ?)
                """,
                (
                    task_id,
                    mode,
                    instruction,
                    json.dumps(scope, ensure_ascii=False),
                    budget_usd,
                    now,
                    now,
                ),
            )
        task = self.get(task_id)
        assert task is not None
        return task

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return _decode_task(row) if row else None

    def list(self, *, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT * FROM tasks
            WHERE (:status IS NULL OR status = :status)
            ORDER BY created_at DESC
            LIMIT :limit
            """,
            {"status": status, "limit": max(1, min(limit, 200))},
        ).fetchall()
        return [_decode_task(row) for row in rows]

    def set_status(
        self,
        task_id: str,
        status: str,
        *,
        stage: str | None = None,
        progress: float | None = None,
        error: str | None = None,
        result_path: str | None = None,
        checkpoint_path: str | None = None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = ?,
                    stage = COALESCE(?, stage),
                    progress = COALESCE(?, progress),
                    error = COALESCE(?, error),
                    result_path = COALESCE(?, result_path),
                    checkpoint_path = COALESCE(?, checkpoint_path),
                    started_at = CASE WHEN ? = 'running' AND started_at IS NULL
                                      THEN ? ELSE started_at END,
                    finished_at = CASE WHEN ? IN
                        ('completed', 'failed', 'cancelled', 'interrupted')
                        THEN ? ELSE finished_at END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    stage,
                    progress,
                    error,
                    result_path,
                    checkpoint_path,
                    status,
                    utc_now(),
                    status,
                    utc_now(),
                    utc_now(),
                    task_id,
                ),
            )

    def set_counts(
        self,
        task_id: str,
        *,
        total: int | None = None,
        done: int | None = None,
        failed: int | None = None,
        progress: float | None = None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET total_batches = COALESCE(?, total_batches),
                    done_batches = COALESCE(?, done_batches),
                    failed_batches = COALESCE(?, failed_batches),
                    progress = COALESCE(?, progress),
                    updated_at = ?
                WHERE id = ?
                """,
                (total, done, failed, progress, utc_now(), task_id),
            )

    def set_budget(self, task_id: str, budget_usd: float | None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET budget_usd = ? WHERE id = ?", (budget_usd, task_id)
            )

    def set_checkpoint(self, task_id: str, path: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET checkpoint_path = ?, updated_at = ? WHERE id = ?",
                (path, utc_now(), task_id),
            )

    def interrupt_stale(self) -> int:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'interrupted', updated_at = ?,
                    error = COALESCE(error, '服务重启，任务已中断；可从检查点继续')
                WHERE status IN ('queued', 'running')
                """,
                (utc_now(),),
            )
            return cursor.rowcount

    # -- batches -----------------------------------------------------------
    def add_batches(self, task_id: str, batches: list[dict[str, Any]]) -> None:
        if not batches:
            return
        now = utc_now()
        rows = []
        for batch in batches:
            rows.append(
                (
                    uuid.uuid4().hex[:16],
                    task_id,
                    batch["batch_index"],
                    batch.get("document_id"),
                    batch.get("label"),
                    json.dumps(batch.get("segments") or [], ensure_ascii=False),
                    batch.get("page_start"),
                    batch.get("page_end"),
                    now,
                )
            )
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO task_batches(
                    id, task_id, batch_index, document_id, label, segments,
                    page_start, page_end, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                rows,
            )

    def batches(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT * FROM task_batches
            WHERE task_id = ?
            ORDER BY batch_index
            """,
            (task_id,),
        ).fetchall()
        return [_decode_batch(row) for row in rows]

    def batch(self, batch_id: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            "SELECT * FROM task_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        return _decode_batch(row) if row else None

    def update_batch(
        self,
        batch_id: str,
        *,
        status: str,
        note_path: str | None = None,
        error: str | None = None,
        input_tokens: int | None = None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE task_batches
                SET status = ?,
                    note_path = COALESCE(?, note_path),
                    error = ?,
                    input_tokens = COALESCE(?, input_tokens),
                    updated_at = ?
                WHERE id = ?
                """,
                (status, note_path, error, input_tokens, utc_now(), batch_id),
            )

    def batch_counts(self, task_id: str) -> dict[str, int]:
        row = self.db.connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS done,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
            FROM task_batches
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}  # noqa: SIM118

    # -- events ------------------------------------------------------------
    def add_event(self, task_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO task_events(task_id, type, payload, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (task_id, event_type, json.dumps(payload, ensure_ascii=False), now),
            )
            event_id = int(cursor.lastrowid or 0)
        return {"id": event_id, "task_id": task_id, "type": event_type, "payload": payload, "created_at": now}

    def events_after(self, task_id: str, after_id: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT * FROM task_events
            WHERE task_id = ? AND id > ?
            ORDER BY id
            LIMIT ?
            """,
            (task_id, after_id, max(1, min(limit, 2000))),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            result.append(item)
        return result

    def recent_events(self, task_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT * FROM task_events
            WHERE task_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (task_id, max(1, limit)),
        ).fetchall()
        result = []
        for row in reversed(rows):
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            result.append(item)
        return result

    # -- analysis cache ----------------------------------------------------
    def cache_get(self, key: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            "SELECT * FROM analysis_cache WHERE key = ?", (key,)
        ).fetchone()
        return dict(row) if row else None

    def cache_put(
        self,
        *,
        key: str,
        input_hash: str,
        purpose: str,
        model: str | None,
        output: str,
        task_id: str | None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO analysis_cache(key, task_id, input_hash, purpose, model, output, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    output = excluded.output,
                    model = excluded.model,
                    created_at = excluded.created_at
                """,
                (key, task_id, input_hash, purpose, model, output, utc_now()),
            )


def _decode_task(row) -> dict[str, Any]:
    data = dict(row)
    data["scope"] = json.loads(data["scope_json"]) if data.get("scope_json") else {}
    return data


def _decode_batch(row) -> dict[str, Any]:
    data = dict(row)
    data["segments"] = json.loads(data["segments"]) if data.get("segments") else []
    return data