from __future__ import annotations

from pathlib import Path

from backend.storage.db import Database


def test_migrations_and_kv(tmp_path: Path) -> None:
    db = Database(tmp_path / "db" / "test.sqlite")
    db.init()
    assert db.user_version == 5
    assert db.ping()

    assert db.kv_get("missing") is None
    db.kv_set("theme", "dark")
    assert db.kv_get("theme") == "dark"
    db.kv_set("theme", "light")
    assert db.kv_get("theme") == "light"
    db.kv_delete("theme")
    assert db.kv_get("theme") is None

    # init() is idempotent.
    db.init()
    assert db.user_version == 5


def test_usage_accounting(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite")
    db.init()
    db.record_usage(
        endpoint_id="llm-1",
        endpoint_kind="llm",
        model="test-model",
        task_id="task-1",
        calls=2,
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.012,
    )
    db.record_usage(
        endpoint_id="llm-1",
        endpoint_kind="llm",
        model="test-model",
        task_id="task-2",
        calls=1,
        input_tokens=10,
        output_tokens=5,
        cost_estimate=None,
    )

    summary = db.usage_summary("task-1")
    assert len(summary) == 1
    assert summary[0]["calls"] == 2
    assert summary[0]["input_tokens"] == 100

    all_rows = db.usage_summary()
    assert all_rows[0]["calls"] == 3
    assert all_rows[0]["cost_estimate"] == 0.012