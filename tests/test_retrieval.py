from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path

import httpx
import pytest
import respx

from backend.core.config import AppConfig, EndpointConfig

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
DIMS = 32


def char_vector(text: str) -> list[float]:
    vector = [0.0] * DIMS
    for index, char in enumerate(text):
        vector[ord(char) % DIMS] += 1.0
        if index + 1 < len(text):
            pair = text[index : index + 2]
            vector[sum(ord(char) for char in pair) % DIMS] += 0.5
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def embedding_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    inputs = body.get("input") or []
    return httpx.Response(
        200,
        json={
            "model": body.get("model"),
            "data": [
                {"index": index, "embedding": char_vector(text)}
                for index, text in enumerate(inputs)
            ],
            "usage": {"prompt_tokens": sum(len(text) for text in inputs)},
        },
    )


def rerank_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    query = body.get("query") or ""
    documents = body.get("documents") or []
    query_chars = set(query)
    scored = []
    for index, document in enumerate(documents):
        score = len(query_chars & set(document)) / max(1, len(query_chars))
        scored.append({"index": index, "relevance_score": score})
    scored.sort(key=lambda item: item["relevance_score"], reverse=True)
    return httpx.Response(200, json={"results": scored})


def endpoint(kind: str, endpoint_id: str, model: str, path: str) -> EndpointConfig:
    return EndpointConfig(
        id=endpoint_id,
        kind=kind,
        provider="openai_compat" if kind != "reranker" else "rerank_http",
        base_url="https://models.test/v1",
        model=model,
        dimensions=DIMS if kind == "embedding" else None,
        batch_size=2,
        timeout_s=10,
    )


def make_config(*, embedding_model: str = "emb-a", embedding_id: str = "emb") -> AppConfig:
    return AppConfig(
        endpoints=[
            endpoint("embedding", embedding_id, embedding_model, "embeddings"),
            endpoint("reranker", "rr", "rerank-model", "rerank"),
        ],
        default_embedding=embedding_id,
        default_reranker="rr",
    )


async def wait_job(index, job_id: str, timeout: float = 40.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = index.get_job(job_id)
        assert job is not None
        if job["status"] in TERMINAL:
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("索引任务超时")


def seed_documents(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "历史.md").write_text(
        "# 第一章 秦统一\n\n秦始皇统一六国，推行郡县制，统一文字与度量衡。",
        encoding="utf-8",
    )
    (folder / "地理.md").write_text(
        "# 第一章 地球运动\n\n地球自转产生昼夜交替，地球公转形成四季变化。",
        encoding="utf-8",
    )
    (folder / "生物.md").write_text(
        "# 第一章 细胞\n\n光合作用发生在叶绿体中，把光能转化为化学能。",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_hybrid_search_with_two_profiles_and_degradation(runtime, tmp_path: Path) -> None:
    index = runtime.index
    retrieval = runtime.retrieval
    assert index is not None and retrieval is not None
    runtime.save_config(make_config())

    folder = tmp_path / "资料"
    seed_documents(folder)

    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://models.test/v1/embeddings").mock(side_effect=embedding_handler)
        mock.post("https://models.test/v1/rerank").mock(side_effect=rerank_handler)

        source = index.add_source(str(folder))
        jobs = index.start_scan(source_id=source["id"])
        job = await wait_job(index, jobs[0]["id"])
        assert job["status"] == "completed", job

        documents = index.documents.list()
        assert len(documents) == 3
        versions = index.documents.current_versions()
        assert all(row["vector_status"] == "ready" for row in versions)
        assert all(row["vector_count"] > 0 for row in versions)
        first_profiles = {row["embedding_profile_id"] for row in versions}
        assert len(first_profiles) == 1

        result = await retrieval.search("地球自转")
        assert result["hits"], result
        top = result["hits"][0]
        assert "地球自转" in top["text"]
        assert top["document_path"].endswith("地理.md")
        assert top["section_path"] == ["第一章 地球运动"]
        assert "昼夜交替" in top["context"]
        assert result["debug"]["reranked"] is True
        assert result["debug"]["profiles"][0]["model"] == "emb-a"

        # -- second embedding model with the SAME dimensions ---------------
        config = make_config(embedding_model="emb-b", embedding_id="emb2")
        runtime.save_config(config)

        geography = index.documents.get_by_path(str(folder / "地理.md"))
        assert geography is not None
        jobs = index.start_scan(document_id=geography["id"], force=True)
        await wait_job(index, jobs[0]["id"])

        reindexed = index.documents.current_versions()
        profiles = {row["embedding_profile_id"] for row in reindexed}
        assert len(profiles) == 2, "同维度不同模型必须使用不同向量空间"

        result = await retrieval.search("秦统一")
        used = {entry["model"] for entry in result["debug"]["profiles"]}
        assert used == {"emb-a", "emb-b"}
        assert result["hits"]

        # -- remove the old endpoint: text/BM25 must remain usable ----------
        reduced = AppConfig(
            endpoints=[
                endpoint("embedding", "emb2", "emb-b", "embeddings"),
                endpoint("reranker", "rr", "rerank-model", "rerank"),
            ],
            default_embedding="emb2",
            default_reranker="rr",
        )
        runtime.save_config(reduced)

        result = await retrieval.search("秦统一")
        assert result["hits"], "旧向量不可用时 BM25 仍应召回原文"
        degraded = " ".join(result["debug"]["degraded"])
        assert "已删除" in degraded or "删除" in degraded
        assert result["debug"]["profiles"]

        # -- rebuild vectors for a document without reparsing ---------------
        history = index.documents.get_by_path(str(folder / "历史.md"))
        assert history is not None
        jobs = index.start_vector_rebuild(document_id=history["id"])
        await wait_job(index, jobs[0]["id"])
        versions = index.documents.versions(history["id"])
        current = next(
            row for row in versions if row["id"] == history["current_version_id"]
        )
        assert current["vector_status"] == "ready"
        assert current["vector_count"] > 0

    await runtime.aclose()