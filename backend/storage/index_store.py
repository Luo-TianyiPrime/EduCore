"""Index persistence: versions, sections, blocks, chunks and FTS tables."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from backend.core.indexing.fts import tokenized_text
from backend.storage.db import Database


class IndexStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- structure ---------------------------------------------------------
    def insert_sections(self, rows: Sequence[Sequence[Any]]) -> None:
        if not rows:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO sections(
                    id, version_id, parent_id, order_index, level, title, page_start, page_end
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def insert_blocks(self, rows: Sequence[Sequence[Any]]) -> None:
        if not rows:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO blocks(
                    id, version_id, section_id, order_index, kind, text, page, bbox, meta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def insert_chunks(self, rows: Sequence[Sequence[Any]]) -> None:
        if not rows:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO chunks(
                    id, version_id, section_id, order_index, heading, text,
                    token_count, page_start, page_end, block_ids
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    # -- full-text ---------------------------------------------------------
    def index_version_fts(self, version_id: str) -> int:
        rows = self.db.connection.execute(
            """
            SELECT rowid AS rid, id, heading, text
            FROM chunks
            WHERE version_id = ?
            ORDER BY order_index
            """,
            (version_id,),
        ).fetchall()
        if not rows:
            return 0

        tokens_rows: list[tuple[int, str, str]] = []
        trigram_rows: list[tuple[int, str, str]] = []
        for row in rows:
            combined = f"{row['heading']}\n{row['text']}".strip()
            tokens_rows.append((int(row["rid"]), row["id"], tokenized_text(combined)))
            trigram_rows.append((int(row["rid"]), row["id"], combined))

        with self.db.transaction() as conn:
            conn.executemany(
                "INSERT INTO chunks_fts(rowid, chunk_id, tokens) VALUES (?, ?, ?)",
                tokens_rows,
            )
            conn.executemany(
                "INSERT INTO chunks_tri(rowid, chunk_id, text) VALUES (?, ?, ?)",
                trigram_rows,
            )
        return len(rows)

    def delete_version_fts(self, version_id: str) -> None:
        rows = self.db.connection.execute(
            "SELECT rowid AS rid FROM chunks WHERE version_id = ?", (version_id,)
        ).fetchall()
        if not rows:
            return
        ids = [(int(row["rid"]),) for row in rows]
        with self.db.transaction() as conn:
            conn.executemany("DELETE FROM chunks_fts WHERE rowid = ?", ids)
            conn.executemany("DELETE FROM chunks_tri WHERE rowid = ?", ids)

    def search_fts(self, match_query: str, limit: int = 100) -> list[dict[str, Any]]:
        if not match_query.strip():
            return []
        rows = self.db.connection.execute(
            """
            SELECT c.id AS chunk_id, c.version_id, c.section_id, c.heading, c.text,
                   c.token_count, c.page_start, c.page_end,
                   bm25(chunks_fts) AS bm25_score,
                   d.id AS document_id, d.title AS document_title, d.path AS document_path
            FROM chunks_fts
            JOIN chunks c ON c.rowid = chunks_fts.rowid
            JOIN document_versions v ON v.id = c.version_id
            JOIN documents d ON d.id = v.document_id
            WHERE chunks_fts MATCH ?
              AND d.is_present = 1
              AND d.current_version_id = c.version_id
              AND d.parse_status IN ('ready', 'needs_ocr')
            ORDER BY bm25_score
            LIMIT ?
            """,
            (match_query, max(1, limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def search_trigram(self, text: str, limit: int = 100) -> list[dict[str, Any]]:
        query = (text or "").strip().replace('"', '""')
        if len(query) < 3:
            return []
        rows = self.db.connection.execute(
            """
            SELECT c.id AS chunk_id, c.version_id, c.section_id, c.heading, c.text,
                   c.token_count, c.page_start, c.page_end,
                   bm25(chunks_tri) AS bm25_score,
                   d.id AS document_id, d.title AS document_title, d.path AS document_path
            FROM chunks_tri
            JOIN chunks c ON c.rowid = chunks_tri.rowid
            JOIN document_versions v ON v.id = c.version_id
            JOIN documents d ON d.id = v.document_id
            WHERE chunks_tri MATCH ?
              AND d.is_present = 1
              AND d.current_version_id = c.version_id
              AND d.parse_status IN ('ready', 'needs_ocr')
            ORDER BY bm25_score
            LIMIT ?
            """,
            (f'"{query}"', max(1, limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def search_fts_scoped(
        self, match_query: str, version_ids: Sequence[str], limit: int = 100
    ) -> list[dict[str, Any]]:
        if not match_query.strip() or not version_ids:
            return []
        conn = self.db.connection
        conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS scope_versions(version_id TEXT PRIMARY KEY)"
        )
        conn.execute("DELETE FROM scope_versions")
        with self.db.transaction() as tx:
            tx.executemany(
                "INSERT OR IGNORE INTO scope_versions(version_id) VALUES (?)",
                [(value,) for value in version_ids],
            )
        rows = conn.execute(
            """
            SELECT c.id AS chunk_id, c.version_id, c.section_id, c.heading, c.text,
                   c.token_count, c.page_start, c.page_end,
                   bm25(chunks_fts) AS bm25_score,
                   d.id AS document_id, d.title AS document_title, d.path AS document_path
            FROM chunks_fts
            JOIN chunks c ON c.rowid = chunks_fts.rowid
            JOIN scope_versions sv ON sv.version_id = c.version_id
            JOIN document_versions v ON v.id = c.version_id
            JOIN documents d ON d.id = v.document_id
            WHERE chunks_fts MATCH ?
            ORDER BY bm25_score
            LIMIT ?
            """,
            (match_query, max(1, limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def search_trigram_scoped(
        self, text: str, version_ids: Sequence[str], limit: int = 100
    ) -> list[dict[str, Any]]:
        query = (text or "").strip().replace('"', '""')
        if len(query) < 3 or not version_ids:
            return []
        conn = self.db.connection
        conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS scope_versions(version_id TEXT PRIMARY KEY)"
        )
        conn.execute("DELETE FROM scope_versions")
        with self.db.transaction() as tx:
            tx.executemany(
                "INSERT OR IGNORE INTO scope_versions(version_id) VALUES (?)",
                [(value,) for value in version_ids],
            )
        rows = conn.execute(
            """
            SELECT c.id AS chunk_id, c.version_id, c.section_id, c.heading, c.text,
                   c.token_count, c.page_start, c.page_end,
                   bm25(chunks_tri) AS bm25_score,
                   d.id AS document_id, d.title AS document_title, d.path AS document_path
            FROM chunks_tri
            JOIN chunks c ON c.rowid = chunks_tri.rowid
            JOIN scope_versions sv ON sv.version_id = c.version_id
            JOIN document_versions v ON v.id = c.version_id
            JOIN documents d ON d.id = v.document_id
            WHERE chunks_tri MATCH ?
            ORDER BY bm25_score
            LIMIT ?
            """,
            (f'"{query}"', max(1, limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    # -- helpers -----------------------------------------------------------
    def structure_counts(self, version_id: str) -> dict[str, int]:
        row = self.db.connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM sections WHERE version_id = ?) AS sections,
                (SELECT COUNT(*) FROM blocks WHERE version_id = ?) AS blocks,
                (SELECT COUNT(*) FROM chunks WHERE version_id = ?) AS chunks
            """,
            (version_id, version_id, version_id),
        ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}  # noqa: SIM118


def dump_bbox(bbox: tuple[float, float, float, float] | None) -> str | None:
    return json.dumps(list(bbox)) if bbox else None