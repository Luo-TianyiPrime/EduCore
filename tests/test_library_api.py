from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def wait_job(client: TestClient, job_id: str, timeout: float = 40.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/index/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()["job"]
        if job["status"] in TERMINAL:
            return job
        time.sleep(0.1)
    raise AssertionError("索引任务超时")


def test_library_flow(client: TestClient, tmp_path: Path) -> None:
    folder = tmp_path / "资料"
    folder.mkdir()
    (folder / "note.md").write_text(
        "# 第一章 概述\n\n这是一份用于接口测试的教研资料，包含光合作用相关内容。",
        encoding="utf-8",
    )

    response = client.post(
        "/api/sources", json={"path": str(folder), "recursive": True, "scan": True}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source"]["path"].endswith("资料")
    job = wait_job(client, body["jobs"][0]["id"])
    assert job["status"] == "completed", job

    sources = client.get("/api/sources").json()
    assert len(sources["items"]) == 1
    assert sources["items"][0]["document_count"] == 1
    assert sources["items"][0]["ready_count"] == 1

    listing = client.get("/api/documents").json()
    assert len(listing["items"]) == 1
    document = listing["items"][0]
    assert document["parse_status"] == "ready"

    detail = client.get(f"/api/documents/{document['id']}").json()
    assert detail["version"] is not None
    assert detail["sections"][0]["title"] == "第一章 概述"

    section_id = detail["sections"][0]["id"]
    blocks = client.get(f"/api/documents/{document['id']}/sections/{section_id}").json()
    assert any("光合作用" in block["text"] for block in blocks["blocks"])

    # Markdown has no page image; the endpoint must say so instead of erroring.
    image = client.get(f"/api/documents/{document['id']}/pages/1")
    assert image.status_code == 404

    # Reindex is accepted and produces a new version.
    reindex = client.post(f"/api/documents/{document['id']}/reindex")
    assert reindex.status_code == 202
    wait_job(client, reindex.json()["jobs"][0]["id"])
    versions = client.get(f"/api/documents/{document['id']}").json()["versions"]
    assert len(versions) == 2

    # Removing a source removes index rows but not the user's files.
    assert client.delete(f"/api/sources/{sources['items'][0]['id']}").status_code == 204
    assert (folder / "note.md").exists()
    assert client.get("/api/documents").json()["items"] == []


def test_add_missing_folder_is_rejected(client: TestClient, tmp_path: Path) -> None:
    response = client.post("/api/sources", json={"path": str(tmp_path / "nope")})
    assert response.status_code == 400


def test_cannot_add_data_directory(client: TestClient, data_dir: Path) -> None:
    response = client.post("/api/sources", json={"path": str(data_dir)})
    assert response.status_code == 400
    assert "数据目录" in response.json()["detail"]