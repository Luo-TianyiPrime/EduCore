from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
import respx
from pydantic_ai.models.test import TestModel

from backend.core.config import AppConfig, EndpointConfig

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def llm_config(*, context_limit: int = 4000, price: float | None = None) -> AppConfig:
    endpoint = EndpointConfig(
        id="llm",
        kind="llm",
        provider="openai_compat",
        base_url="https://models.test/v1",
        model="fake-chat",
        context_limit=context_limit,
        timeout_s=10,
        price_input_per_mtok=price,
        price_output_per_mtok=price,
    )
    return AppConfig(endpoints=[endpoint], default_llm="llm")


def chat_response(model: str, content: str, prompt_tokens: int = 80, output_tokens: int = 40):
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": output_tokens},
        },
    )


def batch_text_of(body: dict) -> str:
    for message in body.get("messages") or []:
        content = message.get("content")
        if isinstance(content, str) and "批次原文" in content:
            return content
    return ""


def make_handler(state: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        text = batch_text_of(body)
        if text:
            state["batch_calls"] += 1
            if state.get("fail_first") and state["batch_calls"] <= 3:
                return httpx.Response(500, json={"error": "synthetic failure"})
            note = {
                "summary": "本批次介绍秦统一与郡县制。",
                "key_points": [{"point": "秦推行郡县制", "section": "秦统一", "page": 1}],
                "evidence": [{"quote": "秦始皇统一六国", "section": "秦统一", "page": 1}],
                "uncertainties": [],
            }
            return chat_response(body.get("model", "fake-chat"), json.dumps(note, ensure_ascii=False))
        return chat_response(
            body.get("model", "fake-chat"),
            "# 教学大纲\n\n## 第一单元\n\n- 秦统一与郡县制（《中国历史》第 1 页）\n\n## 来源\n- 中国历史.md",
        )

    return handler


async def wait_task(runtime, task_id: str, timeout: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = runtime.tasks_repo.get(task_id)
        assert task is not None
        if task["status"] in TERMINAL:
            return task
        await asyncio.sleep(0.1)
    raise AssertionError(f"任务超时: {runtime.tasks_repo.get(task_id)}")


@pytest.mark.asyncio
async def test_full_read_task_completes_with_checkpointed_batches(runtime, tmp_path: Path) -> None:
    runtime.save_config(llm_config())
    index = runtime.index
    assert index is not None
    folder = tmp_path / "资料"
    folder.mkdir()
    body = "秦始皇统一六国，推行郡县制，统一文字与度量衡。" * 30
    for name in ("历史.md", "地理.md"):
        (folder / name).write_text(
            f"# 第一章 {name[:2]}\n\n{body}\n\n## 第二节 影响\n\n{body}\n\n## 第三节 评价\n\n{body}",
            encoding="utf-8",
        )
    source = index.add_source(str(folder))
    jobs = index.start_scan(source_id=source["id"])
    while True:
        job = index.get_job(jobs[0]["id"])
        if job and job["status"] in TERMINAL:
            break
        await asyncio.sleep(0.1)
    assert job["status"] == "completed"

    state = {"batch_calls": 0}
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://models.test/v1/chat/completions").mock(side_effect=make_handler(state))
        task = runtime.task_runner.create_task(
            instruction="请对整本资料生成教学大纲，列出重点与课时建议。",
            document_ids=[document["id"] for document in index.documents.list()],
        )
        assert task["mode"] == "full", task["mode"]
        final = await wait_task(runtime, task["id"])

    assert final["status"] == "completed", final
    counts = runtime.tasks_repo.batch_counts(task["id"])
    assert counts["total"] >= 2
    assert counts["done"] == counts["total"]
    assert counts["failed"] == 0
    assert state["batch_calls"] == counts["total"]

    draft = Path(final["result_path"])
    assert draft.is_file()
    assert "教学大纲" in draft.read_text(encoding="utf-8")

    notes = list((runtime.paths.tasks_dir / task["id"] / "notes").glob("batch_*.json"))
    assert len(notes) == counts["total"]

    events = runtime.tasks_repo.recent_events(task["id"], limit=200)
    types = {event["type"] for event in events}
    assert {"status", "batch", "progress", "artifact"} <= types

    usage = runtime.db.usage_summary(task["id"])
    assert usage and usage[0]["calls"] >= counts["total"]

    artifacts = runtime.artifacts.list(task_id=task["id"])
    assert len(artifacts) == 1, "任务完成后应自动登记成果"
    assert artifacts[0]["title"]

    await runtime.aclose()


@pytest.mark.asyncio
async def test_partial_failure_and_retry(runtime, tmp_path: Path) -> None:
    runtime.save_config(llm_config())
    index = runtime.index
    assert index is not None
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "book.md").write_text(
        "# 第一章\n\n" + "内容段落。" * 200 + "\n\n## 第二章\n\n" + "内容段落。" * 200,
        encoding="utf-8",
    )
    source = index.add_source(str(folder))
    jobs = index.start_scan(source_id=source["id"])
    while True:
        job = index.get_job(jobs[0]["id"])
        if job and job["status"] in TERMINAL:
            break
        await asyncio.sleep(0.1)

    document = index.documents.list()[0]
    state = {"batch_calls": 0, "fail_first": True}
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://models.test/v1/chat/completions").mock(side_effect=make_handler(state))
        task = runtime.task_runner.create_task(
            instruction="整本阅读后生成摘要", document_ids=[document["id"]], mode="full"
        )
        final = await wait_task(runtime, task["id"])

        assert final["status"] == "completed"
        assert "失败" in (final["error"] or "")
        counts = runtime.tasks_repo.batch_counts(task["id"])
        assert counts["failed"] == 1
        draft = Path(final["result_path"]).read_text(encoding="utf-8")
        assert "教学大纲" in draft

        state["fail_first"] = False
        runtime.task_runner.retry(task["id"])
        retried = await wait_task(runtime, task["id"])
        assert retried["status"] == "completed"
        assert runtime.tasks_repo.batch_counts(task["id"])["failed"] == 0

    await runtime.aclose()


@pytest.mark.asyncio
async def test_precise_agent_uses_pydantic_ai_and_saves_draft(
    runtime, tmp_path: Path, monkeypatch
) -> None:
    runtime.save_config(llm_config())
    index = runtime.index
    assert index is not None
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "note.md").write_text("# 概念\n\n光合作用把光能转化为化学能。", encoding="utf-8")
    source = index.add_source(str(folder))
    jobs = index.start_scan(source_id=source["id"])
    while True:
        job = index.get_job(jobs[0]["id"])
        if job and job["status"] in TERMINAL:
            break
        await asyncio.sleep(0.1)

    monkeypatch.setattr(
        "backend.core.agents.runner.build_model",
        lambda endpoint, api_key: TestModel(
            call_tools=[], custom_output_text="# 回答\n\n光合作用把光能转化为化学能。"
        ),
    )
    document = index.documents.list()[0]
    task = runtime.task_runner.create_task(
        instruction="概括光合作用的概念", document_ids=[document["id"]]
    )
    assert task["mode"] == "precise"
    final = await wait_task(runtime, task["id"])
    assert final["status"] == "completed", final
    assert "光合作用" in Path(final["result_path"]).read_text(encoding="utf-8")
    assert (runtime.paths.tasks_dir / task["id"] / "agent_messages.json").is_file()

    await runtime.aclose()


@pytest.mark.asyncio
async def test_mixed_mode_reads_all_then_runs_agent(
    runtime, tmp_path: Path, monkeypatch
) -> None:
    runtime.save_config(llm_config())
    index = runtime.index
    assert index is not None
    folder = tmp_path / "mixed"
    folder.mkdir()
    for name in ("教材.md", "真题.md"):
        (folder / name).write_text(
            f"# {name}\n\n" + "秦统一六国，推行郡县制。" * 80, encoding="utf-8"
        )
    source = index.add_source(str(folder))
    jobs = index.start_scan(source_id=source["id"])
    while True:
        job = index.get_job(jobs[0]["id"])
        if job and job["status"] in TERMINAL:
            break
        await asyncio.sleep(0.1)

    monkeypatch.setattr(
        "backend.core.agents.runner.build_model",
        lambda endpoint, api_key: TestModel(
            call_tools=[], custom_output_text="# 综合成果\n\n结合教材与真题的结论。"
        ),
    )
    state = {"batch_calls": 0}
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://models.test/v1/chat/completions").mock(side_effect=make_handler(state))
        documents = index.documents.list()
        task = runtime.task_runner.create_task(
            instruction="比较教材与真题中的秦统一表述，生成综合讲义",
            document_ids=[document["id"] for document in documents],
        )
        assert task["mode"] == "mixed", task["mode"]
        final = await wait_task(runtime, task["id"])

    assert final["status"] == "completed", final
    assert state["batch_calls"] > 0
    assert "综合成果" in Path(final["result_path"]).read_text(encoding="utf-8")

    await runtime.aclose()


@pytest.mark.asyncio
async def test_pause_and_resume_from_checkpoint(runtime, tmp_path: Path) -> None:
    runtime.save_config(llm_config(context_limit=2400))
    index = runtime.index
    assert index is not None
    folder = tmp_path / "pause"
    folder.mkdir()
    (folder / "long.md").write_text(
        "# 第一章\n\n"
        + "秦统一六国，推行郡县制。" * 400
        + "\n\n## 第二章\n\n"
        + "地球自转产生昼夜交替。" * 400,
        encoding="utf-8",
    )
    source = index.add_source(str(folder))
    jobs = index.start_scan(source_id=source["id"])
    while True:
        job = index.get_job(jobs[0]["id"])
        if job and job["status"] in TERMINAL:
            break
        await asyncio.sleep(0.1)

    gate = asyncio.Event()
    state = {"batch_calls": 0, "gate_used": False}
    base_handler = make_handler(state)

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        if not state["gate_used"]:
            state["gate_used"] = True
            await gate.wait()
        return base_handler(request)

    document = index.documents.list()[0]
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://models.test/v1/chat/completions").mock(side_effect=slow_handler)
        task = runtime.task_runner.create_task(
            instruction="整本阅读后生成完整摘要", document_ids=[document["id"]], mode="full"
        )
        for _ in range(200):
            if state["gate_used"]:
                break
            await asyncio.sleep(0.05)
        assert state["gate_used"], "首个批次未开始"

        runtime.task_runner.pause(task["id"])
        gate.set()

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = runtime.tasks_repo.get(task["id"])
            if current and current["status"] == "paused":
                break
            await asyncio.sleep(0.1)
        paused = runtime.tasks_repo.get(task["id"])
        assert paused is not None and paused["status"] == "paused", paused
        counts = runtime.tasks_repo.batch_counts(task["id"])
        assert 1 <= counts["done"] < counts["total"]
        assert paused["checkpoint_path"] and Path(paused["checkpoint_path"]).is_file()

        runtime.task_runner.resume(task["id"])
        final = await wait_task(runtime, task["id"])

    assert final["status"] == "completed", final
    final_counts = runtime.tasks_repo.batch_counts(task["id"])
    assert final_counts["done"] == final_counts["total"]
    assert final_counts["done"] > counts["done"], "恢复后不应重做全部批次"

    await runtime.aclose()


@pytest.mark.asyncio
async def test_resume_after_process_restart(tmp_path: Path) -> None:
    from backend.core.runtime import AppRuntime

    data_dir = tmp_path / "restart-data"
    runtime = AppRuntime.create(data_dir)
    try:
        runtime.save_config(llm_config(context_limit=2400))
        index = runtime.index
        assert index is not None
        folder = tmp_path / "restart-source"
        folder.mkdir()
        (folder / "book.md").write_text(
            "# 第一章\n\n" + "郡县制与分封制的比较。" * 500,
            encoding="utf-8",
        )
        source = index.add_source(str(folder))
        jobs = index.start_scan(source_id=source["id"])
        while True:
            job = index.get_job(jobs[0]["id"])
            if job and job["status"] in TERMINAL:
                break
            await asyncio.sleep(0.1)

        gate = asyncio.Event()
        state = {"batch_calls": 0, "gate_used": False}
        base_handler = make_handler(state)

        async def slow_handler(request: httpx.Request) -> httpx.Response:
            if not state["gate_used"]:
                state["gate_used"] = True
                await gate.wait()
            return base_handler(request)

        document = index.documents.list()[0]
        with respx.mock(assert_all_called=False) as mock:
            mock.post("https://models.test/v1/chat/completions").mock(side_effect=slow_handler)
            task = runtime.task_runner.create_task(
                instruction="整本阅读后生成摘要", document_ids=[document["id"]], mode="full"
            )
            for _ in range(200):
                if state["gate_used"]:
                    break
                await asyncio.sleep(0.05)
            runtime.task_runner.pause(task["id"])
            gate.set()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                current = runtime.tasks_repo.get(task["id"])
                if current and current["status"] == "paused":
                    break
                await asyncio.sleep(0.1)
            paused = runtime.tasks_repo.get(task["id"])
            assert paused is not None and paused["status"] == "paused"
            task_id = task["id"]
            done_before = runtime.tasks_repo.batch_counts(task_id)["done"]
            assert done_before >= 1
    finally:
        await runtime.aclose()

    # -- simulate a fresh process on the same data directory ----------------
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://models.test/v1/chat/completions").mock(
            side_effect=make_handler({"batch_calls": 0})
        )
        restarted = AppRuntime.create(data_dir)
        try:
            recovered = restarted.tasks_repo.get(task_id)
            assert recovered is not None
            assert recovered["status"] in {"paused", "interrupted"}
            restarted.task_runner.resume(task_id)
            final = await wait_task(restarted, task_id)
        finally:
            await restarted.aclose()

    assert final["status"] == "completed", final
    counts = {"done": final["done_batches"]}
    assert counts["done"] >= done_before