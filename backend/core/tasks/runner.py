"""Task orchestration: scope resolution, batch execution, checkpoints and controls."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.core.agents.deps import AgentDeps
from backend.core.agents.runner import AgentRunner, build_scope_instructions
from backend.core.config import AppConfig
from backend.core.models.gateway import ModelGateway, model_task_scope
from backend.core.models.types import ModelError, ModelNotConfigured
from backend.core.processing.batching import batch_text, build_batches
from backend.core.processing.reading import FullReadEngine
from backend.core.tasks.events import EventBus
from backend.platform.paths import DataPaths
from backend.storage.db import Database
from backend.storage.documents import DocumentRepo
from backend.storage.tasks import TaskRepo

logger = logging.getLogger(__name__)

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
FULL_KEYWORDS = ("整本", "全书", "全部章节", "所有章节", "完整遍历", "全部内容", "完整阅读", "全书总结")
MIXED_KEYWORDS = ("比较", "对比", "结合", "综合", "联合", "交叉", "对照")


class BudgetExceeded(Exception):
    pass


class TaskRunner:
    def __init__(
        self,
        *,
        db: Database,
        paths: DataPaths,
        gateway: ModelGateway,
        documents: DocumentRepo,
        retrieval: Any,
        tasks: TaskRepo,
        events: EventBus,
        config_provider: Callable[[], AppConfig],
        agent_runner: AgentRunner,
        reading: FullReadEngine,
        artifacts: Any | None = None,
    ) -> None:
        self.db = db
        self.paths = paths
        self.gateway = gateway
        self.documents = documents
        self.retrieval = retrieval
        self.tasks = tasks
        self.events = events
        self.config_provider = config_provider
        self.agent_runner = agent_runner
        self.reading = reading
        self.artifacts = artifacts

        self._running: dict[str, asyncio.Task] = {}
        self._pause_requested: set[str] = set()
        self._cancel_requested: set[str] = set()

    # -- creation ----------------------------------------------------------
    def create_task(
        self,
        *,
        instruction: str,
        document_ids: list[str] | None = None,
        source_ids: list[str] | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        text = (instruction or "").strip()
        if not text:
            raise ValueError("任务要求不能为空")
        inputs = self._resolve_inputs(document_ids or [], source_ids or [])
        if not inputs:
            raise ValueError("所选范围内没有已完成的文档，请先添加资料并建立索引")

        resolved_mode = mode if mode in {"precise", "full", "mixed"} else "auto"
        reason = ""
        if resolved_mode == "auto":
            resolved_mode, reason = _decide_mode(text, len(inputs))

        config = self.config_provider()
        scope = {
            "document_ids": [item["document_id"] for item in inputs],
            "source_ids": source_ids or [],
            "inputs": inputs,
            "mode_reason": reason,
        }
        task = self.tasks.create(
            mode=resolved_mode,
            instruction=text,
            scope=scope,
            budget_usd=config.task_budget_usd,
        )
        self.events.publish(
            task["id"],
            "status",
            {"status": "queued", "mode": resolved_mode, "reason": reason},
        )
        self._spawn(task["id"])
        return self.tasks.get(task["id"]) or task

    def _resolve_inputs(
        self, document_ids: list[str], source_ids: list[str]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if document_ids:
            for document_id in document_ids:
                document = self.documents.get(document_id)
                if document:
                    rows.append(document)
        elif source_ids:
            for source_id in source_ids:
                rows.extend(
                    self.documents.list(source_id=source_id, include_missing=False, limit=2000)
                )
        else:
            rows.extend(self.documents.list(include_missing=False, limit=2000))

        inputs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for document in rows:
            if document["id"] in seen or not document["is_present"]:
                continue
            if not document["current_version_id"]:
                continue
            if document["parse_status"] not in {"ready", "needs_ocr"}:
                continue
            seen.add(document["id"])
            inputs.append(
                {
                    "document_id": document["id"],
                    "version_id": document["current_version_id"],
                    "title": document["title"],
                    "path": document["path"],
                    "format": document["format"],
                    "page_count": document["page_count"],
                }
            )
        return inputs

    def _spawn(self, task_id: str) -> None:
        if task_id in self._running:
            return
        task = asyncio.create_task(self._run(task_id))
        self._running[task_id] = task

        def _cleanup(_task: asyncio.Task) -> None:
            self._running.pop(task_id, None)
            self._pause_requested.discard(task_id)
            self._cancel_requested.discard(task_id)

        task.add_done_callback(_cleanup)

    # -- execution ---------------------------------------------------------
    async def _run(self, task_id: str) -> None:
        try:
            with model_task_scope(task_id):
                await self._execute(task_id)
        except asyncio.CancelledError:
            cancelled = task_id in self._cancel_requested
            self.tasks.set_status(
                task_id,
                "cancelled" if cancelled else "interrupted",
                stage="cancelled" if cancelled else "interrupted",
                error="任务已取消" if cancelled else "服务正在关闭，任务已中断，可从检查点继续",
            )
            self.events.publish(
                task_id,
                "status",
                {
                    "status": "cancelled" if cancelled else "interrupted",
                    "message": "任务已取消" if cancelled else "服务关闭，任务已中断",
                },
            )
            raise
        except BudgetExceeded:
            self.tasks.set_status(
                task_id,
                "failed",
                stage="budget",
                error="已达到任务费用预算上限，可在提高预算后重试",
            )
            self.events.publish(
                task_id, "error", {"message": "已达到任务费用预算上限，任务已停止"}
            )
        except ModelNotConfigured as exc:
            self.tasks.set_status(task_id, "failed", stage="failed", error=exc.message)
            self.events.publish(task_id, "error", {"message": exc.message})
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("任务执行失败 %s", task_id)
            message = exc.message if isinstance(exc, ModelError) else f"{type(exc).__name__}: {exc}"
            self.tasks.set_status(task_id, "failed", stage="failed", error=message[:1000])
            self.events.publish(task_id, "error", {"message": message})

    async def _execute(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if task is None or task["status"] in TERMINAL:
            return
        self.tasks.set_status(task["id"], "running", stage="preparing")
        self.events.publish(task["id"], "status", {"status": "running", "stage": "preparing"})
        self.agent_runner.max_steps = max(1, self.config_provider().max_task_steps)

        endpoint = self.gateway.resolve("llm")
        scope = task["scope"]
        inputs = scope.get("inputs") or []
        task_dir = self.paths.tasks_dir / task["id"]
        task_dir.mkdir(parents=True, exist_ok=True)

        deps = AgentDeps(
            gateway=self.gateway,
            documents=self.documents,
            retrieval=self.retrieval,
            events=self.events,
            task_id=task["id"],
            task_dir=task_dir,
            inputs=inputs,
            tasks=self.tasks,
        )

        mode = task["mode"]
        if mode in {"full", "mixed"}:
            await self._prepare_batches(task, deps, endpoint)
            await self._run_batches(task, deps, endpoint)
            if task["id"] in self._pause_requested:
                self._pause(task)
                return
            notes = self._load_notes(task)
            if mode == "mixed":
                digest = self._notes_digest(notes)
                extra = self._mixed_instructions(deps, digest)
                await self._run_agent(task, deps, endpoint, extra_instructions=extra)
            else:
                await self._synthesize(task, deps, endpoint, notes)
        else:
            await self._run_agent(task, deps, endpoint)

        if task["id"] in self._pause_requested:
            self._pause(task)
            return
        self._complete(task)

    # -- batches -----------------------------------------------------------
    async def _prepare_batches(
        self, task: dict[str, Any], deps: AgentDeps, endpoint
    ) -> None:
        existing = self.tasks.batches(task["id"])
        if existing:
            self.tasks.set_counts(task["id"], total=len(existing))
            return
        context = endpoint.context_limit or 16000
        target = max(1200, int(context * 0.4))
        hard_max = max(target, int(context * 0.55))
        drafts: list[dict[str, Any]] = []
        start = 0
        for item in deps.inputs:
            try:
                parsed = deps.snapshot(item["document_id"])
            except (ValueError, PermissionError) as exc:
                self.events.publish(
                    task["id"],
                    "log",
                    {"message": f"跳过无法读取的文档 {item.get('title')}: {exc}"},
                )
                continue
            document_batches = build_batches(
                parsed,
                document_id=item["document_id"],
                document_title=item.get("title") or item.get("path") or "",
                target_tokens=target,
                max_tokens=hard_max,
                start_index=start,
            )
            start += len(document_batches)
            drafts.extend(
                {
                    "batch_index": batch.batch_index,
                    "document_id": batch.document_id,
                    "label": batch.label,
                    "segments": batch.segments,
                    "page_start": batch.page_start,
                    "page_end": batch.page_end,
                }
                for batch in document_batches
            )
        self.tasks.add_batches(task["id"], drafts)
        if not drafts:
            raise ValueError("范围内没有可读取的文本批次，无法执行全量阅读")
        self.tasks.set_counts(task["id"], total=len(drafts), done=0, failed=0, progress=2)
        self.events.publish(
            task["id"],
            "stage",
            {"stage": "reading", "total_batches": len(drafts)},
        )

    async def _run_batches(self, task: dict[str, Any], deps: AgentDeps, endpoint) -> None:
        batches = self.tasks.batches(task["id"])
        total = len(batches)
        if total == 0:
            return
        done = sum(1 for batch in batches if batch["status"] in {"completed", "skipped"})
        failed = sum(1 for batch in batches if batch["status"] == "failed")
        for batch in batches:
            if batch["status"] != "pending":
                continue
            if task["id"] in self._pause_requested:
                return
            if task["id"] in self._cancel_requested:
                raise asyncio.CancelledError
            self.tasks.update_batch(batch["id"], status="running")
            self.events.publish(
                task["id"],
                "batch",
                {
                    "batch_index": batch["batch_index"],
                    "total": total,
                    "status": "running",
                    "label": batch["label"],
                },
            )
            started = time.perf_counter()
            try:
                parsed = deps.snapshot(batch["document_id"])
                text = batch_text(parsed, batch["segments"])
                if not text.strip():
                    self.tasks.update_batch(batch["id"], status="skipped")
                    done += 1
                    continue
                note = await self.reading.analyze_batch(
                    task_id=task["id"],
                    instruction=task["instruction"],
                    batch=batch,
                    parsed=parsed,
                    document_title=deps.document_title(batch["document_id"]),
                    endpoint=endpoint,
                    text=text,
                )
                path = self.reading.save_note(task["id"], note)
                tokens = note.get("input_tokens") or 0
                self.tasks.update_batch(
                    batch["id"], status="completed", note_path=str(path), input_tokens=int(tokens)
                )
                done += 1
            except Exception as exc:
                failed += 1
                logger.warning("批次分析失败 %s: %s", batch["id"], exc)
                self.tasks.update_batch(
                    batch["id"],
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}"[:500],
                )
                self.events.publish(
                    task["id"],
                    "batch",
                    {
                        "batch_index": batch["batch_index"],
                        "total": total,
                        "status": "failed",
                        "label": batch["label"],
                        "error": str(exc)[:300],
                    },
                )
            progress = 5 + (done + failed) / max(1, total) * 80
            self.tasks.set_counts(
                task["id"], done=done, failed=failed, progress=round(progress, 1)
            )
            self.events.publish(
                task["id"],
                "progress",
                {
                    "done_batches": done,
                    "failed_batches": failed,
                    "total_batches": total,
                    "progress": round(progress, 1),
                    "last_batch_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            self._write_checkpoint(task, {"stage": "reading", "done": done, "failed": failed})
            self._check_budget(task)

    def _load_notes(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        notes: list[dict[str, Any]] = []
        for batch in self.tasks.batches(task["id"]):
            if batch["status"] == "completed" and batch.get("note_path"):
                path = Path(batch["note_path"])
                if path.is_file():
                    try:
                        notes.append(json.loads(path.read_text(encoding="utf-8")))
                        continue
                    except ValueError:
                        pass
            if batch["status"] == "failed":
                notes.append(
                    {
                        "batch_index": batch["batch_index"],
                        "label": batch["label"],
                        "summary": "",
                        "key_points": [],
                        "evidence": [],
                        "uncertainties": [f"批次分析失败：{batch.get('error') or '未知错误'}"],
                        "document_title": None,
                        "page_start": batch.get("page_start"),
                        "page_end": batch.get("page_end"),
                    }
                )
        return notes

    def _notes_digest(self, notes: list[dict[str, Any]], max_chars: int = 14000) -> str:
        lines: list[str] = []
        for note in notes:
            lines.append(
                f"### 批次 {note.get('batch_index')} · {note.get('label') or note.get('document_title') or ''}"
            )
            if note.get("summary"):
                lines.append(note["summary"])
            for point in note.get("key_points") or []:
                if isinstance(point, dict):
                    lines.append(
                        f"- {point.get('point')}（{point.get('section') or ''} {point.get('page') or ''}）"
                    )
                elif isinstance(point, str):
                    lines.append(f"- {point}")
            for item in note.get("uncertainties") or []:
                lines.append(f"- 不确定：{item}")
        digest = "\n".join(lines)
        return digest[:max_chars]

    # -- agent / synthesis -------------------------------------------------
    def _mixed_instructions(self, deps: AgentDeps, digest: str) -> str:
        scope_text = build_scope_instructions(deps)
        return (
            f"{scope_text}\n\n"
            "本任务已完成对核心资料的全量阅读，以下是批次笔记摘要：\n"
            f"{digest}\n\n"
            "请在此基础上使用检索、章节阅读等工具补充与核对证据，生成最终成果。"
            "最终完整成果必须通过 write_draft 工具保存，回复中只需一句完成说明。"
        )

    async def _run_agent(
        self,
        task: dict[str, Any],
        deps: AgentDeps,
        endpoint,
        *,
        extra_instructions: str = "",
    ) -> None:
        self.tasks.set_status(task["id"], "running", stage="agent", progress=55)
        self.events.publish(task["id"], "stage", {"stage": "agent"})
        scope_text = build_scope_instructions(deps)
        prompt = f"{scope_text}\n\n用户任务：\n{task['instruction']}"
        if not extra_instructions:
            prompt += (
                "\n\n请自主调用工具完成分析与写作；最终完整成果必须通过 write_draft 工具保存，"
                "回复中只需一句完成说明。"
            )
        result = await self.agent_runner.run(
            endpoint=endpoint,
            deps=deps,
            instruction=prompt,
            extra_instructions=extra_instructions,
        )
        self._save_messages(task["id"], result.get("messages") or [])
        self._check_budget(task)
        draft_path = deps.task_dir / "output" / "draft.md"
        if not draft_path.is_file():
            self._write_draft(task["id"], result["text"])
        self.events.publish(
            task["id"],
            "log",
            {"message": f"Agent 完成，模型调用 {result['usage'].get('requests', 0)} 次"},
        )

    async def _synthesize(
        self,
        task: dict[str, Any],
        deps: AgentDeps,
        endpoint,
        notes: list[dict[str, Any]],
    ) -> None:
        self.tasks.set_status(task["id"], "running", stage="synthesis", progress=90)
        self.events.publish(task["id"], "stage", {"stage": "synthesis"})
        text = await self.reading.synthesize(
            task_id=task["id"],
            instruction=task["instruction"],
            scope_inputs=deps.inputs,
            notes=notes,
            endpoint=endpoint,
        )
        self._check_budget(task)
        self._write_draft(task["id"], text)

    def _write_draft(self, task_id: str, markdown: str) -> Path:
        directory = self.paths.tasks_dir / task_id / "output"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "draft.md"
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(markdown, encoding="utf-8")
        tmp.replace(path)
        self.events.publish(task_id, "artifact", {"path": str(path), "chars": len(markdown)})
        return path

    def _save_messages(self, task_id: str, messages: list[dict[str, Any]]) -> None:
        directory = self.paths.tasks_dir / task_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "agent_messages.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(messages, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    # -- completion / control ----------------------------------------------
    def _complete(self, task: dict[str, Any]) -> None:
        counts = self.tasks.batch_counts(task["id"])
        draft = self.paths.tasks_dir / task["id"] / "output" / "draft.md"
        if not draft.is_file():
            self.tasks.set_status(
                task["id"], "failed", stage="completed", error="未生成成果草稿"
            )
            self.events.publish(task["id"], "error", {"message": "未生成成果草稿"})
            return
        message = None
        if counts["failed"]:
            message = f"{counts['failed']} 个批次失败，成果未覆盖这些部分"
        artifact_id = None
        if self.artifacts is not None:
            try:
                artifact = self.artifacts.create_from_task(task, draft.read_text(encoding="utf-8"))
                artifact_id = artifact["id"]
            except Exception as exc:  # pragma: no cover - artifact failure must not hide the draft
                logger.exception("成果记录创建失败 %s", task["id"])
                self.events.publish(
                    task["id"], "error", {"message": f"成果记录创建失败：{exc}"}
                )
        self.tasks.set_status(
            task["id"],
            "completed",
            stage="completed",
            progress=100,
            error=message,
            result_path=str(draft),
        )
        self._write_checkpoint(task, {"stage": "completed", "result": str(draft)})
        self.events.publish(
            task["id"],
            "status",
            {
                "status": "completed",
                "result_path": str(draft),
                "message": message,
                "failed_batches": counts["failed"],
                "total_batches": counts["total"],
                "artifact_id": artifact_id,
            },
        )

    def _pause(self, task: dict[str, Any]) -> None:
        self.tasks.set_status(task["id"], "paused", stage="paused")
        self._write_checkpoint(task, {"stage": "paused"})
        self.events.publish(
            task["id"], "status", {"status": "paused", "message": "任务已暂停，可从检查点继续"}
        )

    def _write_checkpoint(self, task: dict[str, Any], extra: dict[str, Any]) -> Path:
        directory = self.paths.tasks_dir / task["id"]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "checkpoint.json"
        counts = self.tasks.batch_counts(task["id"])
        payload = {
            "task_id": task["id"],
            "mode": task["mode"],
            "instruction": task["instruction"],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "batches": counts,
            "notes": [
                batch["note_path"]
                for batch in self.tasks.batches(task["id"])
                if batch.get("note_path")
            ],
            **extra,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        self.tasks.set_checkpoint(task["id"], str(path))
        return path

    def _check_budget(self, task: dict[str, Any]) -> None:
        budget = task.get("budget_usd")
        if not budget:
            return
        cost = sum(
            float(row["cost_estimate"] or 0)
            for row in self.db.usage_summary(task["id"])
        )
        if cost > float(budget):
            raise BudgetExceeded()

    def _control(self, task_id: str, action: str) -> dict[str, Any]:
        task = self.tasks.get(task_id)
        if task is None:
            raise ValueError("任务不存在")
        if action == "pause":
            if task["status"] not in {"queued", "running"}:
                raise ValueError("当前状态不能暂停")
            self._pause_requested.add(task_id)
            if task["status"] == "queued":
                self.tasks.set_status(task_id, "paused", stage="paused")
                self.events.publish(task_id, "status", {"status": "paused"})
        elif action == "resume":
            if task["status"] not in {"paused", "interrupted"}:
                raise ValueError("当前状态不能继续")
            self._pause_requested.discard(task_id)
            self.tasks.set_status(task_id, "queued", stage="queued")
            self.events.publish(task_id, "status", {"status": "queued", "message": "从检查点继续"})
            self._spawn(task_id)
        elif action == "cancel":
            if task["status"] in TERMINAL:
                raise ValueError("任务已结束")
            self._cancel_requested.add(task_id)
            running = self._running.get(task_id)
            if running is not None:
                running.cancel()
            else:
                self.tasks.set_status(task_id, "cancelled", stage="cancelled", error="任务已取消")
                self.events.publish(task_id, "status", {"status": "cancelled"})
        elif action == "retry":
            counts = self.tasks.batch_counts(task_id)
            can_retry = task["status"] in {"failed", "cancelled", "interrupted"} or (
                task["status"] == "completed" and counts["failed"] > 0
            )
            if not can_retry:
                raise ValueError("当前状态不能重试")
            for batch in self.tasks.batches(task_id):
                if batch["status"] == "failed":
                    self.tasks.update_batch(batch["id"], status="pending", error=None)
            self._pause_requested.discard(task_id)
            self._cancel_requested.discard(task_id)
            self.tasks.set_status(task_id, "queued", stage="queued", error=None)
            self.events.publish(task_id, "status", {"status": "queued", "message": "任务重试"})
            self._spawn(task_id)
        else:
            raise ValueError(f"未知操作: {action}")
        return self.tasks.get(task_id) or task

    def pause(self, task_id: str) -> dict[str, Any]:
        return self._control(task_id, "pause")

    def resume(self, task_id: str) -> dict[str, Any]:
        return self._control(task_id, "resume")

    def cancel(self, task_id: str) -> dict[str, Any]:
        return self._control(task_id, "cancel")

    def retry(self, task_id: str) -> dict[str, Any]:
        return self._control(task_id, "retry")

    def interrupt_stale(self) -> int:
        return self.tasks.interrupt_stale()

    async def aclose(self) -> None:
        tasks = list(self._running.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._running.clear()


def _decide_mode(instruction: str, document_count: int) -> tuple[str, str]:
    if any(keyword in instruction for keyword in FULL_KEYWORDS):
        return "full", "检测到全量/整本阅读要求"
    if document_count > 1 and any(keyword in instruction for keyword in MIXED_KEYWORDS):
        return "mixed", "涉及多来源综合或对照"
    return "precise", "局部查询与精读"