"""Persistent embedding profile registry.

A profile identifies one vector space: endpoint + model + dimensions +
instructions + distance.  Tables are never shared across profiles, even when
dimensions coincide.
"""

from __future__ import annotations

import hashlib
from typing import Any

from backend.core.config import EndpointConfig
from backend.storage.db import Database, utc_now


def profile_signature(
    *,
    endpoint_id: str,
    model: str,
    dimensions: int,
    distance: str,
    query_instruction: str | None,
    document_instruction: str | None,
) -> str:
    payload = "\x1f".join(
        [
            endpoint_id,
            model,
            str(dimensions),
            distance,
            query_instruction or "",
            document_instruction or "",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class EmbeddingProfilesRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, profile_id: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            "SELECT * FROM embedding_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        return dict(row) if row else None

    def list(self) -> list[dict[str, Any]]:
        rows = self.db.connection.execute(
            """
            SELECT p.*,
                   (SELECT COUNT(*) FROM document_versions v
                     WHERE v.embedding_profile_id = p.id) AS version_count
            FROM embedding_profiles p
            ORDER BY p.created_at
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def ensure(self, endpoint: EndpointConfig, dimensions: int) -> dict[str, Any]:
        profile_id = profile_signature(
            endpoint_id=endpoint.id,
            model=endpoint.model,
            dimensions=dimensions,
            distance=endpoint.distance,
            query_instruction=endpoint.query_instruction,
            document_instruction=endpoint.document_instruction,
        )
        existing = self.get(profile_id)
        if existing:
            return existing

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO embedding_profiles(
                    id, endpoint_id, model, dimensions, distance,
                    query_instruction, document_instruction, table_name,
                    status, created_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    profile_id,
                    endpoint.id,
                    endpoint.model,
                    dimensions,
                    endpoint.distance,
                    endpoint.query_instruction,
                    endpoint.document_instruction,
                    f"p_{profile_id}",
                    utc_now(),
                    utc_now(),
                ),
            )
        created = self.get(profile_id)
        assert created is not None
        return created

    def touch(self, profile_id: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE embedding_profiles SET last_used_at = ? WHERE id = ?",
                (utc_now(), profile_id),
            )

    def set_status(self, profile_id: str, status: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE embedding_profiles SET status = ? WHERE id = ?",
                (status, profile_id),
            )