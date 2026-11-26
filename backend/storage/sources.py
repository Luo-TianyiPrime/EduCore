"""Source folder persistence."""

from __future__ import annotations

import uuid
from typing import Any

from backend.storage.db import Database, utc_now


class SourceRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, path: str, recursive: bool = True) -> dict[str, Any]:
        source_id = uuid.uuid4().hex[:16]
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO source_folders(id, path, recursive, created_at, status, error)
                VALUES (?, ?, ?, ?, 'idle', NULL)
                """,
                (source_id, path, 1 if recursive else 0, now),
            )
        result = self.get(source_id)
        assert result is not None
        return result

    def list(self) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT s.id, s.path, s.recursive, s.created_at, s.last_scan_at, s.status, s.error,
                   (SELECT COUNT(*) FROM documents d
                     WHERE d.source_id = s.id AND d.is_present = 1) AS document_count,
                   (SELECT COUNT(*) FROM documents d
                     WHERE d.source_id = s.id AND d.is_present = 1
                       AND d.parse_status = 'ready') AS ready_count,
                   (SELECT COUNT(*) FROM documents d
                     WHERE d.source_id = s.id AND d.is_present = 1
                       AND d.parse_status = 'needs_ocr') AS needs_ocr_count,
                   (SELECT COUNT(*) FROM documents d
                     WHERE d.source_id = s.id AND d.is_present = 1
                       AND d.parse_status = 'failed') AS failed_count
            FROM source_folders s
            ORDER BY s.created_at
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def get(self, source_id: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            "SELECT * FROM source_folders WHERE id = ?", (source_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_by_path(self, path: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            "SELECT * FROM source_folders WHERE path = ?", (path,)
        ).fetchone()
        return dict(row) if row else None

    def delete(self, source_id: str) -> bool:
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM source_folders WHERE id = ?", (source_id,))
            return cursor.rowcount > 0

    def set_status(self, source_id: str, status: str, error: str | None = None) -> None:
        with self.db.transaction() as conn:
            if status == "idle":
                conn.execute(
                    "UPDATE source_folders SET status = ?, error = ?, last_scan_at = ? WHERE id = ?",
                    (status, error, utc_now(), source_id),
                )
            else:
                conn.execute(
                    "UPDATE source_folders SET status = ?, error = ? WHERE id = ?",
                    (status, error, source_id),
                )