"""Document, section, block and chunk persistence."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from backend.storage.db import Database, utc_now


def _row(row) -> dict[str, Any] | None:
    return dict(row) if row else None


class DocumentRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- documents ---------------------------------------------------------
    def get(self, document_id: str) -> dict[str, Any] | None:
        return _row(
            self.db.connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        )

    def get_by_path(self, path: str) -> dict[str, Any] | None:
        return _row(
            self.db.connection.execute(
                "SELECT * FROM documents WHERE path = ?", (path,)
            ).fetchone()
        )

    def list(
        self,
        *,
        source_id: str | None = None,
        status: str | None = None,
        query: str | None = None,
        include_missing: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        params = {
            "source_id": source_id,
            "status": status,
            "query": query,
            "include_missing": 1 if include_missing else 0,
            "limit": max(1, min(limit, 2000)),
            "offset": max(0, offset),
        }
        rows = self.db.connection.execute(
            """
            SELECT id, source_id, path, rel_path, format, title, size, mtime,
                   page_count, current_version_id, parse_status, parse_error,
                   is_present, created_at, updated_at, content_hash
            FROM documents
            WHERE (:source_id IS NULL OR source_id = :source_id)
              AND (:include_missing = 1 OR is_present = 1)
              AND (:status IS NULL OR parse_status = :status)
              AND (
                    :query IS NULL
                    OR title LIKE '%' || :query || '%'
                    OR path LIKE '%' || :query || '%'
                  )
            ORDER BY updated_at DESC, title
            LIMIT :limit OFFSET :offset
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> dict[str, int]:
        row = self.db.connection.execute(
            """
            SELECT
                SUM(CASE WHEN is_present = 1 THEN 1 ELSE 0 END) AS present,
                SUM(CASE WHEN is_present = 1 AND parse_status = 'ready' THEN 1 ELSE 0 END) AS ready,
                SUM(CASE WHEN is_present = 1 AND parse_status = 'needs_ocr' THEN 1 ELSE 0 END) AS needs_ocr,
                SUM(CASE WHEN is_present = 1 AND parse_status = 'failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN is_present = 0 THEN 1 ELSE 0 END) AS missing
            FROM documents
            """
        ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}  # noqa: SIM118

    def upsert_discovered(
        self,
        *,
        source_id: str | None,
        path: str,
        rel_path: str | None,
        format: str,
        size: int,
        mtime: float,
    ) -> dict[str, Any]:
        existing = self.get_by_path(path)
        now = utc_now()
        if existing:
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    UPDATE documents
                    SET source_id = ?, rel_path = ?, format = ?, size = ?, mtime = ?,
                        is_present = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (source_id, rel_path, format, size, mtime, now, existing["id"]),
                )
            refreshed = self.get(existing["id"])
            assert refreshed is not None
            return refreshed

        document_id = uuid.uuid4().hex[:16]
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO documents(
                    id, source_id, path, rel_path, format, title, content_hash,
                    size, mtime, page_count, current_version_id, parse_status,
                    parse_error, is_present, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, NULL, 'pending', NULL, 1, ?, ?)
                """,
                (
                    document_id,
                    source_id,
                    path,
                    rel_path,
                    format,
                    Path(path).name,
                    size,
                    mtime,
                    now,
                    now,
                ),
            )
        created = self.get(document_id)
        assert created is not None
        return created

    def set_status(
        self,
        document_id: str,
        status: str,
        *,
        error: str | None = None,
        title: str | None = None,
        page_count: int | None = None,
        content_hash: str | None = None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE documents
                SET parse_status = ?,
                    parse_error = ?,
                    title = COALESCE(?, title),
                    page_count = COALESCE(?, page_count),
                    content_hash = COALESCE(?, content_hash),
                    updated_at = ?
                WHERE id = ?
                """,
                (status, error, title, page_count, content_hash, utc_now(), document_id),
            )

    def set_current_version(self, document_id: str, version_id: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE documents SET current_version_id = ?, updated_at = ? WHERE id = ?",
                (version_id, utc_now(), document_id),
            )

    def mark_all_missing(self, source_id: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE documents SET is_present = 0, updated_at = ? WHERE source_id = ?",
                (utc_now(), source_id),
            )

    def restore_present(self, paths: list[str]) -> None:
        if not paths:
            return
        now = utc_now()
        with self.db.transaction() as conn:
            conn.executemany(
                "UPDATE documents SET is_present = 1, updated_at = ? WHERE path = ?",
                [(now, path) for path in paths],
            )

    def delete(self, document_id: str) -> bool:
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            return cursor.rowcount > 0

    # -- versions ----------------------------------------------------------
    def get_version(self, version_id: str) -> dict[str, Any] | None:
        return _row(
            self.db.connection.execute(
                "SELECT * FROM document_versions WHERE id = ?", (version_id,)
            ).fetchone()
        )

    def versions(self, document_id: str) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT * FROM document_versions
            WHERE document_id = ?
            ORDER BY created_at DESC
            """,
            (document_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def create_version(
        self,
        *,
        document_id: str,
        content_hash: str,
        parsed_path: str | None,
        page_count: int,
        block_count: int,
        chunk_count: int,
        version_id: str | None = None,
    ) -> dict[str, Any]:
        version_id = version_id or uuid.uuid4().hex[:16]
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO document_versions(
                    id, document_id, content_hash, parsed_path, page_count,
                    block_count, chunk_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (version_id, document_id, content_hash, parsed_path, page_count, block_count, chunk_count, now),
            )
            conn.execute(
                """
                UPDATE documents
                SET current_version_id = ?, content_hash = ?, page_count = ?, updated_at = ?
                WHERE id = ?
                """,
                (version_id, content_hash, page_count, now, document_id),
            )
        version = self.get_version(version_id)
        assert version is not None
        return version

    def delete_old_versions(self, document_id: str, keep: int = 3) -> list[str]:
        rows = self.db.connection.execute(
            """
            SELECT id FROM document_versions
            WHERE document_id = ?
            ORDER BY created_at DESC
            """,
            (document_id,),
        ).fetchall()
        stale = [row["id"] for row in rows[keep:]]
        if not stale:
            return []
        with self.db.transaction() as conn:
            conn.executemany("DELETE FROM document_versions WHERE id = ?", [(item,) for item in stale])
        return stale

    # -- vector state ------------------------------------------------------
    def set_vector_status(
        self,
        version_id: str,
        *,
        profile_id: str | None,
        status: str,
        count: int,
        error: str | None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE document_versions
                SET embedding_profile_id = ?, vector_status = ?, vector_count = ?, vector_error = ?
                WHERE id = ?
                """,
                (profile_id, status, count, error, version_id),
            )

    def current_versions(self) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT v.id AS version_id, v.document_id, v.embedding_profile_id,
                   v.vector_status, v.vector_count, v.page_count, v.block_count,
                   v.chunk_count, d.source_id, d.title, d.path, d.parse_status
            FROM document_versions v
            JOIN documents d ON d.id = v.document_id
            WHERE d.is_present = 1
              AND d.current_version_id = v.id
              AND d.parse_status IN ('ready', 'needs_ocr')
            ORDER BY d.path
            """
        ).fetchall()
        return [dict(row) for row in rows]

    # -- structure ---------------------------------------------------------
    def sections(self, version_id: str) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT * FROM sections
            WHERE version_id = ?
            ORDER BY order_index
            """,
            (version_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def sections_with_counts(self, version_id: str) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT s.*,
                   (SELECT COUNT(*) FROM blocks b WHERE b.section_id = s.id) AS block_count
            FROM sections s
            WHERE s.version_id = ?
            ORDER BY s.order_index
            """,
            (version_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def blocks(self, version_id: str, section_id: str | None = None) -> list[dict[str, Any]]:
        if section_id is None:
            rows = self.db.connection.execute(
                """
                SELECT * FROM blocks WHERE version_id = ? ORDER BY order_index
                """,
                (version_id,),
            ).fetchall()
        else:
            rows = self.db.connection.execute(
                """
                SELECT * FROM blocks WHERE version_id = ? AND section_id = ?
                ORDER BY order_index
                """,
                (version_id, section_id),
            ).fetchall()
        return [_decode_block(row) for row in rows]

    def block(self, block_id: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            "SELECT * FROM blocks WHERE id = ?", (block_id,)
        ).fetchone()
        return _decode_block(row) if row else None

    def chunks(self, version_id: str) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT * FROM chunks WHERE version_id = ? ORDER BY order_index
            """,
            (version_id,),
        ).fetchall()
        return [_decode_chunk(row) for row in rows]

    def chunks_by_ids(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        if not chunk_ids:
            return []
        conn = self.db.connection
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS lookup_chunk_ids(id TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM lookup_chunk_ids")
        with self.db.transaction() as tx:
            tx.executemany(
                "INSERT OR IGNORE INTO lookup_chunk_ids(id) VALUES (?)",
                [(chunk_id,) for chunk_id in chunk_ids],
            )
        rows = conn.execute(
            """
            SELECT c.*, v.document_id,
                   d.title AS document_title, d.path AS document_path,
                   d.format AS document_format,
                   d.current_version_id, d.source_id
            FROM chunks c
            JOIN document_versions v ON v.id = c.version_id
            JOIN documents d ON d.id = v.document_id
            JOIN lookup_chunk_ids l ON l.id = c.id
            """
        ).fetchall()
        return [_decode_chunk(row) for row in rows]

    def neighbor_chunks(
        self, version_id: str, order_index: int, radius: int = 1
    ) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT * FROM chunks
            WHERE version_id = ? AND order_index BETWEEN ? AND ?
            ORDER BY order_index
            """,
            (version_id, order_index - radius, order_index + radius),
        ).fetchall()
        return [_decode_chunk(row) for row in rows]

    def section(self, section_id: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            "SELECT * FROM sections WHERE id = ?", (section_id,)
        ).fetchone()
        return dict(row) if row else None

    def chunk(self, chunk_id: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            """
            SELECT c.*, v.document_id, d.title AS document_title, d.path AS document_path
            FROM chunks c
            JOIN document_versions v ON v.id = c.version_id
            JOIN documents d ON d.id = v.document_id
            WHERE c.id = ?
            """,
            (chunk_id,),
        ).fetchone()
        return _decode_chunk(row) if row else None


def _decode_block(row) -> dict[str, Any]:
    data = dict(row)
    data["bbox"] = json.loads(data["bbox"]) if data.get("bbox") else None
    data["meta"] = json.loads(data["meta"]) if data.get("meta") else {}
    return data


def _decode_chunk(row) -> dict[str, Any]:
    data = dict(row)
    data["block_ids"] = json.loads(data["block_ids"]) if data.get("block_ids") else []
    return data