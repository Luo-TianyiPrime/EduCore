"""Artifact metadata, version and export persistence."""

from __future__ import annotations

import json
import uuid
from typing import Any

from backend.storage.db import Database, utc_now


class ArtifactRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        *,
        title: str,
        markdown: str,
        structure_json: str,
        task_id: str | None,
        kind: str = "document",
    ) -> dict[str, Any]:
        artifact_id = uuid.uuid4().hex[:16]
        version_id = uuid.uuid4().hex[:16]
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO artifacts(id, task_id, title, kind, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, task_id, title, kind, now, now),
            )
            conn.execute(
                """
                INSERT INTO artifact_versions(
                    id, artifact_id, version, markdown, structure_json, instruction, created_at
                ) VALUES (?, ?, 1, ?, ?, NULL, ?)
                """,
                (version_id, artifact_id, markdown, structure_json, now),
            )
        artifact = self.get(artifact_id)
        assert artifact is not None
        return artifact

    def get(self, artifact_id: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            """
            SELECT a.*,
                   (SELECT id FROM artifact_versions v
                     WHERE v.artifact_id = a.id ORDER BY v.version DESC LIMIT 1) AS version_id,
                   (SELECT version FROM artifact_versions v
                     WHERE v.artifact_id = a.id ORDER BY v.version DESC LIMIT 1) AS version
            FROM artifacts a WHERE a.id = ?
            """,
            (artifact_id,),
        ).fetchone()
        return dict(row) if row else None

    def list(self, *, task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT a.*,
                   (SELECT version FROM artifact_versions v
                     WHERE v.artifact_id = a.id ORDER BY v.version DESC LIMIT 1) AS version,
                   (SELECT created_at FROM artifact_versions v
                     WHERE v.artifact_id = a.id ORDER BY v.version DESC LIMIT 1) AS version_created_at
            FROM artifacts a
            WHERE (:task_id IS NULL OR a.task_id = :task_id)
            ORDER BY a.updated_at DESC
            LIMIT :limit
            """,
            {"task_id": task_id, "limit": max(1, min(limit, 500))},
        ).fetchall()
        return [dict(row) for row in rows]

    def current_version(self, artifact_id: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            """
            SELECT * FROM artifact_versions
            WHERE artifact_id = ?
            ORDER BY version DESC LIMIT 1
            """,
            (artifact_id,),
        ).fetchone()
        return dict(row) if row else None

    def versions(self, artifact_id: str) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT id, artifact_id, version, instruction, created_at,
                   LENGTH(markdown) AS chars
            FROM artifact_versions
            WHERE artifact_id = ?
            ORDER BY version DESC
            """,
            (artifact_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_version(
        self,
        artifact_id: str,
        *,
        markdown: str,
        structure_json: str,
        instruction: str | None,
    ) -> dict[str, Any]:
        version_id = uuid.uuid4().hex[:16]
        now = utc_now()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next FROM artifact_versions WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            next_version = int(row["next"])
            conn.execute(
                """
                INSERT INTO artifact_versions(
                    id, artifact_id, version, markdown, structure_json, instruction, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (version_id, artifact_id, next_version, markdown, structure_json, instruction, now),
            )
            conn.execute(
                "UPDATE artifacts SET updated_at = ? WHERE id = ?", (now, artifact_id)
            )
        created = self.current_version(artifact_id)
        assert created is not None
        return created

    def rename(self, artifact_id: str, title: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE artifacts SET title = ?, updated_at = ? WHERE id = ?",
                (title, utc_now(), artifact_id),
            )

    def record_export(self, artifact_id: str, format: str, path: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO artifact_exports(id, artifact_id, format, path, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex[:16], artifact_id, format, path, utc_now()),
            )

    def latest_export(self, artifact_id: str, format: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            """
            SELECT * FROM artifact_exports
            WHERE artifact_id = ? AND format = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (artifact_id, format),
        ).fetchone()
        return dict(row) if row else None


def structure_to_json(document: Any) -> str:
    return json.dumps(document.to_dict(), ensure_ascii=False)