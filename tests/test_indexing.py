from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from backend.core.indexing.fts import build_match_query

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


async def wait_for_job(index, job_id: str, timeout: float = 40.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = index.get_job(job_id)
        assert job is not None
        if job["status"] in TERMINAL:
            return job
        await asyncio.sleep(0.05)
    raise AssertionError(f"索引任务超时: {index.get_job(job_id)}")


@pytest.mark.asyncio
async def test_scan_index_search_and_incremental(runtime, tmp_path: Path) -> None:
    index = runtime.index
    assert index is not None

    folder = tmp_path / "教学资料"
    folder.mkdir()
    history = folder / "历史.md"
    geography = folder / "地理.md"
    history.write_text(
        "# 第一章 秦统一\n\n秦始皇统一六国，建立郡县制。\n\n## 第二节 影响\n\n统一文字与度量衡。",
        encoding="utf-8",
    )
    geography.write_text(
        "# 第一章 地球运动\n\n地球自转产生昼夜交替，地球公转形成四季变化。",
        encoding="utf-8",
    )

    source = index.add_source(str(folder))
    jobs = index.start_scan(source_id=source["id"])
    job = await wait_for_job(index, jobs[0]["id"])
    assert job["status"] == "completed", job
    assert job["failed"] == 0

    documents = index.documents.list()
    assert len(documents) == 2
    assert all(document["parse_status"] == "ready" for document in documents)

    hits = index.index.search_fts(build_match_query("地球自转"))
    assert hits, "FTS 未召回中文内容"
    assert "地球自转" in hits[0]["text"]
    assert hits[0]["document_title"] == "第一章 地球运动"

    history_doc = index.documents.get_by_path(str(history))
    assert history_doc is not None
    first_version = history_doc["current_version_id"]
    assert first_version
    sections = index.documents.sections_with_counts(first_version)
    assert [section["title"] for section in sections] == ["第一章 秦统一", "第二节 影响"]
    blocks = index.documents.blocks(first_version)
    assert any("郡县制" in block["text"] for block in blocks)

    # -- modify one file: only that document gets a new version -----------
    history.write_text(
        "# 第一章 秦统一\n\n修订版内容：推行郡县制并统一货币。",
        encoding="utf-8",
    )
    jobs = index.start_scan(source_id=source["id"])
    job = await wait_for_job(index, jobs[0]["id"])
    assert job["status"] == "completed"

    updated = index.documents.get_by_path(str(history))
    assert updated is not None
    assert updated["current_version_id"] != first_version
    assert len(index.documents.versions(updated["id"])) == 2

    new_hits = index.index.search_fts(build_match_query("统一货币"))
    assert new_hits
    old_hits = index.index.search_fts(build_match_query("统一文字"))
    assert only_current_versions(old_hits, updated["id"], updated["current_version_id"])

    # -- rename one file: old path is marked missing, new path is indexed ----
    renamed = folder / "地理-重命名.md"
    geography.rename(renamed)
    jobs = index.start_scan(source_id=source["id"])
    await wait_for_job(index, jobs[0]["id"])
    old = index.documents.get_by_path(str(geography))
    new = index.documents.get_by_path(str(renamed))
    assert old is not None and old["is_present"] == 0
    assert new is not None and new["is_present"] == 1
    assert new["parse_status"] == "ready"

    # -- delete one file: rows stay for history but stop being searchable --
    renamed.unlink()
    jobs = index.start_scan(source_id=source["id"])
    await wait_for_job(index, jobs[0]["id"])

    gone = index.documents.get_by_path(str(renamed))
    assert gone is not None
    assert gone["is_present"] == 0
    assert index.index.search_fts(build_match_query("昼夜交替")) == []

    await runtime.aclose()


def only_current_versions(hits: list[dict], document_id: str, version_id: str) -> bool:
    return all(
        not (hit["document_id"] == document_id and hit["version_id"] != version_id)
        for hit in hits
    )


@pytest.mark.asyncio
async def test_document_reindex_creates_new_version(runtime, tmp_path: Path) -> None:
    index = runtime.index
    assert index is not None
    folder = tmp_path / "docs"
    folder.mkdir()
    note = folder / "note.md"
    note.write_text("# 标题\n\n正文内容。", encoding="utf-8")

    source = index.add_source(str(folder))
    jobs = index.start_scan(source_id=source["id"])
    await wait_for_job(index, jobs[0]["id"])

    document = index.documents.get_by_path(str(note))
    assert document is not None
    before = len(index.documents.versions(document["id"]))

    jobs = index.start_scan(document_id=document["id"], force=True)
    await wait_for_job(index, jobs[0]["id"])
    assert len(index.documents.versions(document["id"])) == before + 1

    await runtime.aclose()