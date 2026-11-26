"""Page-level OCR cache keyed by image hash and OCR endpoint."""

from __future__ import annotations

import json
from typing import Any

from backend.storage.db import Database, utc_now


class OcrCacheRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, image_hash: str, endpoint_id: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            """
            SELECT text, blocks_json, created_at
            FROM ocr_cache
            WHERE image_hash = ? AND endpoint_id = ?
            """,
            (image_hash, endpoint_id),
        ).fetchone()
        if not row:
            return None
        return {
            "text": row["text"],
            "blocks": json.loads(row["blocks_json"]) if row["blocks_json"] else [],
            "created_at": row["created_at"],
        }

    def put(
        self,
        *,
        image_hash: str,
        endpoint_id: str,
        text: str,
        blocks: list[dict[str, Any]],
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO ocr_cache(image_hash, endpoint_id, text, blocks_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(image_hash, endpoint_id) DO UPDATE SET
                    text = excluded.text,
                    blocks_json = excluded.blocks_json,
                    created_at = excluded.created_at
                """,
                (image_hash, endpoint_id, text, json.dumps(blocks, ensure_ascii=False), utc_now()),
            )

    def stats(self) -> dict[str, int]:
        row = self.db.connection.execute(
            "SELECT COUNT(*) AS entries FROM ocr_cache"
        ).fetchone()
        return {"entries": int(row["entries"] or 0)}