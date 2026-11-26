"""SQLite storage layer.

A thin wrapper around :mod:`sqlite3` with per-thread connections, WAL mode and
a forward-only migration runner based on ``PRAGMA user_version``.

Security note: this module intentionally exposes no generic
``execute(sql_string)`` helper.  Every statement is a fixed literal written at
its call site, and every value is passed through SQLite parameter binding.
Feature modules add their own literal statements next to the data they own.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _apply_v1(conn: sqlite3.Connection) -> None:
    """Create the P0 schema."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_kv (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint_id TEXT NOT NULL,
            endpoint_kind TEXT NOT NULL,
            model TEXT,
            task_id TEXT,
            calls INTEGER NOT NULL DEFAULT 1,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_estimate REAL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_usage_created ON model_usage(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_usage_endpoint ON model_usage(endpoint_id)")


def _apply_v2(conn: sqlite3.Connection) -> None:
    """P1 schema: sources, documents, versions, structure, chunks, jobs, OCR cache."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_folders (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            recursive INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_scan_at TEXT,
            status TEXT NOT NULL DEFAULT 'idle',
            error TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            source_id TEXT REFERENCES source_folders(id) ON DELETE SET NULL,
            path TEXT NOT NULL UNIQUE,
            rel_path TEXT,
            format TEXT NOT NULL,
            title TEXT,
            content_hash TEXT,
            size INTEGER NOT NULL DEFAULT 0,
            mtime REAL NOT NULL DEFAULT 0,
            page_count INTEGER NOT NULL DEFAULT 0,
            current_version_id TEXT,
            parse_status TEXT NOT NULL DEFAULT 'pending',
            parse_error TEXT,
            is_present INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(parse_status)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_versions (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            content_hash TEXT NOT NULL,
            parsed_path TEXT,
            page_count INTEGER NOT NULL DEFAULT 0,
            block_count INTEGER NOT NULL DEFAULT 0,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_versions_doc ON document_versions(document_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sections (
            id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
            parent_id TEXT,
            order_index INTEGER NOT NULL,
            level INTEGER NOT NULL DEFAULT 1,
            title TEXT NOT NULL,
            page_start INTEGER,
            page_end INTEGER
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sections_version ON sections(version_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS blocks (
            id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
            section_id TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'paragraph',
            text TEXT NOT NULL,
            page INTEGER,
            bbox TEXT,
            meta TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_blocks_version ON blocks(version_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_blocks_section ON blocks(section_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
            section_id TEXT NOT NULL,
            order_index INTEGER NOT NULL,
            heading TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL,
            token_count INTEGER NOT NULL DEFAULT 0,
            page_start INTEGER,
            page_end INTEGER,
            block_ids TEXT NOT NULL DEFAULT '[]'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_version ON chunks(version_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_section ON chunks(section_id)")
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_id UNINDEXED,
            tokens,
            tokenize='unicode61 remove_diacritics 2'
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_tri USING fts5(
            chunk_id UNINDEXED,
            text,
            tokenize='trigram'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ocr_cache (
            image_hash TEXT NOT NULL,
            endpoint_id TEXT NOT NULL,
            text TEXT NOT NULL,
            blocks_json TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (image_hash, endpoint_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS index_jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            source_id TEXT,
            document_id TEXT,
            status TEXT NOT NULL,
            total INTEGER NOT NULL DEFAULT 0,
            processed INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            message TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON index_jobs(status)")


def _apply_v3(conn: sqlite3.Connection) -> None:
    """P2 schema: embedding profiles and per-version vector state."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS embedding_profiles (
            id TEXT PRIMARY KEY,
            endpoint_id TEXT NOT NULL,
            model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            distance TEXT NOT NULL DEFAULT 'cosine',
            query_instruction TEXT,
            document_instruction TEXT,
            table_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            last_used_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_profiles_endpoint ON embedding_profiles(endpoint_id)"
    )
    conn.execute(
        "ALTER TABLE document_versions ADD COLUMN embedding_profile_id TEXT"
    )
    conn.execute(
        "ALTER TABLE document_versions ADD COLUMN vector_status TEXT NOT NULL DEFAULT 'none'"
    )
    conn.execute(
        "ALTER TABLE document_versions ADD COLUMN vector_count INTEGER NOT NULL DEFAULT 0"
    )
    conn.execute(
        "ALTER TABLE document_versions ADD COLUMN vector_error TEXT"
    )


def _apply_v4(conn: sqlite3.Connection) -> None:
    """P3 schema: agent tasks, batches, events and analysis cache."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL DEFAULT 'agent',
            mode TEXT NOT NULL,
            instruction TEXT NOT NULL,
            scope_json TEXT NOT NULL,
            status TEXT NOT NULL,
            progress REAL NOT NULL DEFAULT 0,
            stage TEXT,
            total_batches INTEGER NOT NULL DEFAULT 0,
            done_batches INTEGER NOT NULL DEFAULT 0,
            failed_batches INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            result_path TEXT,
            checkpoint_path TEXT,
            budget_usd REAL,
            cost_estimate REAL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_batches (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            batch_index INTEGER NOT NULL,
            document_id TEXT,
            label TEXT,
            segments TEXT NOT NULL DEFAULT '[]',
            page_start INTEGER,
            page_end INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            input_tokens INTEGER NOT NULL DEFAULT 0,
            note_path TEXT,
            error TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(task_id, batch_index)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_batches_task ON task_batches(task_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            type TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id, id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_cache (
            key TEXT PRIMARY KEY,
            task_id TEXT,
            input_hash TEXT NOT NULL,
            purpose TEXT NOT NULL,
            model TEXT,
            output TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def _apply_v5(conn: sqlite3.Connection) -> None:
    """P4 schema: artifacts, versions and export records."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            title TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'document',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS artifact_versions (
            id TEXT PRIMARY KEY,
            artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            markdown TEXT NOT NULL,
            structure_json TEXT,
            instruction TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(artifact_id, version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS artifact_exports (
            id TEXT PRIMARY KEY,
            artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
            format TEXT NOT NULL,
            path TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifact_exports ON artifact_exports(artifact_id)"
    )


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.local()
        self._write_lock = threading.RLock()

    # -- connections -------------------------------------------------------
    @property
    def connection(self) -> sqlite3.Connection:
        """Per-thread connection; callers execute fixed literal SQL on it."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=10000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def ping(self) -> bool:
        row = self.connection.execute("SELECT 1").fetchone()
        return bool(row and row[0] == 1)

    # -- migrations --------------------------------------------------------
    @property
    def user_version(self) -> int:
        row = self.connection.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def init(self) -> None:
        with self._write_lock:
            conn = self.connection
            version = self.user_version
            if version < 1:
                logger.info("应用数据库迁移 v1")
                conn.execute("BEGIN")
                try:
                    _apply_v1(conn)
                    conn.execute("PRAGMA user_version = 1")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            if version < 2:
                logger.info("应用数据库迁移 v2")
                conn.execute("BEGIN")
                try:
                    _apply_v2(conn)
                    conn.execute("PRAGMA user_version = 2")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            if version < 3:
                logger.info("应用数据库迁移 v3")
                conn.execute("BEGIN")
                try:
                    _apply_v3(conn)
                    conn.execute("PRAGMA user_version = 3")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            if version < 4:
                logger.info("应用数据库迁移 v4")
                conn.execute("BEGIN")
                try:
                    _apply_v4(conn)
                    conn.execute("PRAGMA user_version = 4")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            if version < 5:
                logger.info("应用数据库迁移 v5")
                conn.execute("BEGIN")
                try:
                    _apply_v5(conn)
                    conn.execute("PRAGMA user_version = 5")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            conn = self.connection
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    # -- key/value ---------------------------------------------------------
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM app_kv WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        with self._write_lock:
            self.connection.execute(
                """
                INSERT INTO app_kv(key, value, updated_at) VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                               updated_at = excluded.updated_at
                """,
                (key, value, utc_now()),
            )

    def kv_delete(self, key: str) -> None:
        with self._write_lock:
            self.connection.execute("DELETE FROM app_kv WHERE key = ?", (key,))

    # -- usage -------------------------------------------------------------
    def record_usage(
        self,
        *,
        endpoint_id: str,
        endpoint_kind: str,
        model: str | None,
        task_id: str | None,
        calls: int,
        input_tokens: int,
        output_tokens: int,
        cost_estimate: float | None,
    ) -> None:
        with self._write_lock:
            self.connection.execute(
                """
                INSERT INTO model_usage(
                    endpoint_id, endpoint_kind, model, task_id, calls,
                    input_tokens, output_tokens, cost_estimate, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    endpoint_id,
                    endpoint_kind,
                    model,
                    task_id,
                    calls,
                    input_tokens,
                    output_tokens,
                    cost_estimate,
                    utc_now(),
                ),
            )

    def usage_summary(self, task_id: str | None = None) -> list[dict[str, Any]]:
        if task_id is None:
            rows: Sequence[sqlite3.Row] = self.connection.execute(
                """
                SELECT endpoint_id, endpoint_kind, model,
                       SUM(calls) AS calls,
                       SUM(input_tokens) AS input_tokens,
                       SUM(output_tokens) AS output_tokens,
                       SUM(cost_estimate) AS cost_estimate
                FROM model_usage
                GROUP BY endpoint_id, endpoint_kind, model
                ORDER BY calls DESC
                """
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT endpoint_id, endpoint_kind, model,
                       SUM(calls) AS calls,
                       SUM(input_tokens) AS input_tokens,
                       SUM(output_tokens) AS output_tokens,
                       SUM(cost_estimate) AS cost_estimate
                FROM model_usage
                WHERE task_id = ?
                GROUP BY endpoint_id, endpoint_kind, model
                ORDER BY calls DESC
                """,
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]