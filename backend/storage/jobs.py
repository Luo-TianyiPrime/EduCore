"""Indexing job persistence."""

from __future__ import annotations

import uuid
from typing import Any

from backend.storage.db import Database, utc_now

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


class JobRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        *,
        kind: str,
        source_id: str | None = None,
        document_id: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:16]
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO index_jobs(
                    id, kind, source_id, document_id, status, total, processed,
                    failed, message, created_at
                ) VALUES (?, ?, ?, ?, 'queued', 0, 0, 0, ?, ?)
                """,
                (job_id, kind, source_id, document_id, message, utc_now()),
            )
        job = self.get(job_id)
        assert job is not None
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            "SELECT * FROM index_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT * FROM index_jobs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 200)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def start(self, job_id: str, total: int) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE index_jobs
                SET status = 'running', total = ?, started_at = ?, message = NULL
                WHERE id = ?
                """,
                (total, utc_now(), job_id),
            )

    def progress(
        self,
        job_id: str,
        *,
        processed: int | None = None,
        failed: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE index_jobs
                SET processed = COALESCE(?, processed),
                    failed = COALESCE(?, failed),
                    total = COALESCE(?, total),
                    message = COALESCE(?, message)
                WHERE id = ?
                """,
                (processed, failed, total, message, job_id),
            )

    def finish(
        self,
        job_id: str,
        *,
        status: str,
        message: str | None = None,
        processed: int | None = None,
        failed: int | None = None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE index_jobs
                SET status = ?, message = ?, processed = COALESCE(?, processed),
                    failed = COALESCE(?, failed), finished_at = ?
                WHERE id = ?
                """,
                (status, message, processed, failed, utc_now(), job_id),
            )

    def interrupt_stale(self) -> int:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE index_jobs
                SET status = 'interrupted', finished_at = ?,
                    message = COALESCE(message, '服务重启，任务已中断')
                WHERE status IN ('queued', 'running')
                """,
                (utc_now(),),
            )
            return cursor.rowcount