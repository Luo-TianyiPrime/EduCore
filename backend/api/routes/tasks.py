"""Task endpoints and SSE progress stream."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from backend.api.deps import RuntimeDep
from backend.api.schemas import TaskActionRequest, TaskCreateRequest
from backend.core.tasks.runner import TERMINAL

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _runner(runtime):
    if runtime.task_runner is None:  # pragma: no cover
        raise HTTPException(status_code=500, detail="任务服务未初始化")
    return runtime.task_runner


@router.post("", status_code=201)
async def create_task(payload: TaskCreateRequest, runtime: RuntimeDep) -> dict:
    try:
        task = _runner(runtime).create_task(
            instruction=payload.instruction,
            document_ids=payload.document_ids,
            source_ids=payload.source_ids,
            mode=None if payload.mode == "auto" else payload.mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"task": task}


@router.get("")
def list_tasks(
    runtime: RuntimeDep,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    assert runtime.tasks_repo is not None
    return {"items": runtime.tasks_repo.list(limit=limit, status=status)}


@router.get("/{task_id}")
def get_task(task_id: str, runtime: RuntimeDep) -> dict:
    assert runtime.tasks_repo is not None
    task = runtime.tasks_repo.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "task": task,
        "batches": runtime.tasks_repo.batches(task_id),
        "events": runtime.tasks_repo.recent_events(task_id, limit=80),
        "usage": runtime.db.usage_summary(task_id),
    }


@router.post("/{task_id}/action")
def task_action(task_id: str, payload: TaskActionRequest, runtime: RuntimeDep) -> dict:
    runner = _runner(runtime)
    actions = {
        "pause": runner.pause,
        "resume": runner.resume,
        "cancel": runner.cancel,
        "retry": runner.retry,
    }
    action = actions.get(payload.action)
    if action is None:
        raise HTTPException(status_code=400, detail="未知操作")
    try:
        task = action(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"task": task}


@router.get("/{task_id}/output")
def task_output(task_id: str, runtime: RuntimeDep) -> dict:
    assert runtime.tasks_repo is not None
    task = runtime.tasks_repo.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    path = Path(task["result_path"]) if task.get("result_path") else (
        runtime.paths.tasks_dir / task_id / "output" / "draft.md"
    )
    if not path.is_file():
        raise HTTPException(status_code=404, detail="尚未生成成果")
    return {
        "path": str(path),
        "content": path.read_text(encoding="utf-8"),
        "task_id": task_id,
    }


def _format_event(event: dict) -> str:
    payload = json.dumps(event.get("payload") or {}, ensure_ascii=False)
    return f"id: {event['id']}\nevent: {event['type']}\ndata: {payload}\n\n"


@router.get("/{task_id}/events")
async def task_events(
    task_id: str,
    request: Request,
    runtime: RuntimeDep,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    assert runtime.tasks_repo is not None and runtime.events is not None
    task = runtime.tasks_repo.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    queue = runtime.events.subscribe(task_id)
    after = 0
    try:
        after = int(last_event_id or 0)
    except ValueError:
        after = 0

    async def stream():
        seen: set[int] = set()
        try:
            for event in runtime.tasks_repo.events_after(task_id, after):
                seen.add(event["id"])
                yield _format_event(event)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                else:
                    if event["id"] in seen:
                        continue
                    seen.add(event["id"])
                    yield _format_event(event)
                current = runtime.tasks_repo.get(task_id)
                if current and current["status"] in TERMINAL and queue.empty():
                    yield f"event: done\ndata: {json.dumps({'status': current['status']})}\n\n"
                    break
        finally:
            runtime.events.unsubscribe(task_id, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )