"""LanceDB vector storage, one table per embedding profile."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[0-9a-fA-F]{6,64}$")
_FILTER_BATCH = 400


def _safe_id(value: str) -> str:
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"非法标识符: {value!r}")
    return value


class VectorStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._local = threading.local()

    # -- connection / tables ----------------------------------------------
    @property
    def connection(self):
        connection = getattr(self._local, "connection", None)
        if connection is None:
            import lancedb

            self.root.mkdir(parents=True, exist_ok=True)
            connection = lancedb.connect(str(self.root))
            self._local.connection = connection
        return connection

    def ensure_table(self, table_name: str, dimensions: int):
        import pyarrow as pa

        schema = pa.schema(
            [
                pa.field("chunk_id", pa.string()),
                pa.field("version_id", pa.string()),
                pa.field("document_id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dimensions)),
            ]
        )
        return self.connection.create_table(table_name, schema=schema, exist_ok=True)

    def add(
        self,
        table_name: str,
        dimensions: int,
        rows: Sequence[dict[str, Any]],
    ) -> int:
        if not rows:
            return 0
        table = self.ensure_table(table_name, dimensions)
        table.add(list(rows), mode="append")
        return len(rows)

    def open_table(self, table_name: str):
        try:
            return self.connection.open_table(table_name)
        except Exception:
            return None

    def delete_version(self, table_name: str, version_id: str) -> None:
        table = self.open_table(table_name)
        if table is None:
            return
        table.delete(f"version_id = '{_safe_id(version_id)}'")

    def delete_document_except_version(
        self, table_name: str, document_id: str, keep_version_id: str
    ) -> None:
        table = self.open_table(table_name)
        if table is None:
            return
        clause = (
            f"document_id = '{_safe_id(document_id)}' "
            f"AND version_id != '{_safe_id(keep_version_id)}'"
        )
        table.delete(clause)

    def count(self, table_name: str) -> int:
        table = self.open_table(table_name)
        if table is None:
            return 0
        return int(table.count_rows())

    # -- search ------------------------------------------------------------
    def search(
        self,
        table_name: str,
        vector: list[float],
        *,
        limit: int,
        version_ids: Sequence[str],
        distance: str = "cosine",
    ) -> list[dict[str, Any]]:
        if not version_ids or limit <= 0:
            return []
        table = self.connection.open_table(table_name)
        results: dict[str, dict[str, Any]] = {}
        for start in range(0, len(version_ids), _FILTER_BATCH):
            batch = [_safe_id(value) for value in version_ids[start : start + _FILTER_BATCH]]
            if not batch:
                continue
            joined = ", ".join(f"'{value}'" for value in batch)
            query = (
                table.search(vector)
                .metric(distance)
                .where(f"version_id IN ({joined})")
                .limit(limit)
            )
            for row in query.to_list():
                chunk_id = row["chunk_id"]
                current = results.get(chunk_id)
                if current is None or row.get("_distance", 1e9) < current.get("_distance", 1e9):
                    results[chunk_id] = row
        ordered = sorted(results.values(), key=lambda row: row.get("_distance", 1e9))
        return ordered[:limit]