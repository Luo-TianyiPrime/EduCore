"""Artifact lifecycle: creation from tasks, editing, regeneration and export."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.core.artifacts.exporter import ArtifactExporter
from backend.core.artifacts.model import (
    extract_section,
    parse_artifact,
    replace_section,
)
from backend.core.artifacts.validator import validate
from backend.core.models.gateway import ModelGateway
from backend.core.retrieval.pipeline import RetrievalService
from backend.core.tasks.events import EventBus
from backend.platform.paths import DataPaths
from backend.storage.artifacts import ArtifactRepo, structure_to_json
from backend.storage.db import Database
from backend.storage.tasks import TaskRepo

logger = logging.getLogger(__name__)

_KIND_KEYWORDS = (
    (("大纲", "课时"), "outline"),
    (("教案", "讲义"), "lesson"),
    (("试卷", "出卷", "题目", "评分"), "exam"),
    (("总结",), "summary"),
    (("考点", "分析"), "analysis"),
    (("比较", "对比"), "comparison"),
)


class ArtifactService:
    def __init__(
        self,
        *,
        db: Database,
        paths: DataPaths,
        gateway: ModelGateway,
        retrieval: RetrievalService,
        tasks: TaskRepo,
        events: EventBus,
        exporter: ArtifactExporter | None = None,
    ) -> None:
        self.db = db
        self.paths = paths
        self.gateway = gateway
        self.retrieval = retrieval
        self.tasks = tasks
        self.events = events
        self.repo = ArtifactRepo(db)
        self.exporter = exporter or ArtifactExporter(paths)

    # -- creation ----------------------------------------------------------
    def create(
        self,
        *,
        title: str,
        markdown: str,
        task_id: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        document = parse_artifact(markdown, fallback_title=title)
        resolved_title = document.title.strip() or title
        return self.repo.create(
            title=resolved_title,
            markdown=markdown,
            structure_json=structure_to_json(document),
            task_id=task_id,
            kind=kind or self._infer_kind(title),
        )

    def create_from_task(self, task: dict[str, Any], markdown: str) -> dict[str, Any]:
        title = _title_from_markdown(markdown) or _short_instruction(task["instruction"])
        artifact = self.create(
            title=title,
            markdown=markdown,
            task_id=task["id"],
            kind=self._infer_kind(task["instruction"]),
        )
        self.events.publish(
            task["id"],
            "artifact",
            {"artifact_id": artifact["id"], "title": artifact["title"], "path": None},
        )
        return artifact

    def _infer_kind(self, instruction: str) -> str:
        for keywords, kind in _KIND_KEYWORDS:
            if any(keyword in instruction for keyword in keywords):
                return kind
        return "document"

    # -- reads -------------------------------------------------------------
    def list(self, *, task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.repo.list(task_id=task_id, limit=limit)

    def get(self, artifact_id: str) -> dict[str, Any] | None:
        artifact = self.repo.get(artifact_id)
        if artifact is None:
            return None
        version = self.repo.current_version(artifact_id)
        structure = {}
        if version and version.get("structure_json"):
            try:
                structure = json.loads(version["structure_json"])
            except ValueError:
                structure = {}
        return {
            "artifact": artifact,
            "version": version,
            "structure": structure,
            "versions": self.repo.versions(artifact_id),
        }

    def detail_with_validation(self, artifact_id: str) -> dict[str, Any]:
        detail = self.get(artifact_id)
        if detail is None:
            raise ValueError("成果不存在")
        detail["validation"] = self._validate(artifact_id, detail)
        return detail

    def _validate(self, artifact_id: str, detail: dict[str, Any]) -> dict[str, Any]:
        version = detail["version"]
        artifact = detail["artifact"]
        scope_inputs: list[dict[str, Any]] = []
        instruction = ""
        if artifact.get("task_id"):
            task = self.tasks.get(artifact["task_id"])
            if task:
                scope_inputs = task["scope"].get("inputs") or []
                instruction = task["instruction"]
        document = parse_artifact(version["markdown"], fallback_title=artifact["title"])
        return validate(
            document,
            markdown=version["markdown"],
            instruction=instruction,
            scope_inputs=scope_inputs,
        )

    # -- edits -------------------------------------------------------------
    def update_markdown(self, artifact_id: str, markdown: str) -> dict[str, Any]:
        artifact = self.repo.get(artifact_id)
        if artifact is None:
            raise ValueError("成果不存在")
        document = parse_artifact(markdown, fallback_title=artifact["title"])
        self.repo.add_version(
            artifact_id,
            markdown=markdown,
            structure_json=structure_to_json(document),
            instruction="手动编辑",
        )
        updated = self.get(artifact_id)
        assert updated is not None
        return updated

    async def regenerate_section(
        self,
        artifact_id: str,
        *,
        heading: str,
        instruction: str,
    ) -> dict[str, Any]:
        detail = self.get(artifact_id)
        if detail is None:
            raise ValueError("成果不存在")
        artifact = detail["artifact"]
        version = detail["version"]
        markdown = version["markdown"]

        _level, section_lines = extract_section(markdown, heading)
        existing = "\n".join(section_lines)
        if not instruction.strip():
            raise ValueError("请说明如何修改该章节")

        task = self.tasks.get(artifact["task_id"]) if artifact.get("task_id") else None
        scope_inputs = (task["scope"].get("inputs") or []) if task else []
        task_instruction = task["instruction"] if task else ""

        evidence = ""
        if scope_inputs:
            try:
                result = await self.retrieval.search(
                    f"{heading} {instruction}",
                    document_ids=[item["document_id"] for item in scope_inputs],
                    top_k=6,
                )
                evidence = "\n\n".join(
                    f"[{hit['document_title']} · {' / '.join(hit['section_path'])} · 第 {hit['page_start'] or '?'} 页]\n{hit['text'][:900]}"
                    for hit in result["hits"]
                )
            except Exception as exc:  # pragma: no cover - retrieval failure is non-fatal
                logger.warning("章节重生成检索失败: %s", exc)

        messages = [
            {
                "role": "system",
                "content": (
                    "你是教学文档编辑。只重写用户指定的一个章节，保持标题与层级不变；"
                    "不要输出其他章节、不要写解释。引用原文时保留（《文档名》第 N 页）格式。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"当前章节：\n{existing}\n\n"
                    f"修改要求：\n{instruction}\n\n"
                    f"任务总体要求（仅供一致性参考）：\n{task_instruction}\n\n"
                    f"可参考的原文证据：\n{evidence or '（无检索证据，不得编造引文）'}"
                ),
            },
        ]
        response = await self.gateway.llm(messages, temperature=0.3, max_tokens=4000)
        new_content = response.text.strip()
        if not new_content:
            raise ValueError("模型返回空内容")
        updated_markdown = replace_section(markdown, heading, new_content)
        document = parse_artifact(updated_markdown, fallback_title=artifact["title"])
        self.repo.add_version(
            artifact_id,
            markdown=updated_markdown,
            structure_json=structure_to_json(document),
            instruction=f"局部重生成：{heading}",
        )
        updated = self.get(artifact_id)
        assert updated is not None
        return updated

    def rename(self, artifact_id: str, title: str) -> None:
        self.repo.rename(artifact_id, title)

    # -- export ------------------------------------------------------------
    async def export(
        self,
        artifact_id: str,
        *,
        format: str,
        output_dir: str | None = None,
        include_citations: bool = True,
    ) -> dict[str, Any]:
        if format not in {"md", "docx", "json", "pdf"}:
            raise ValueError(f"不支持的导出格式: {format}")
        detail = self.get(artifact_id)
        if detail is None:
            raise ValueError("成果不存在")
        custom: Path | None = None
        if output_dir:
            candidate = Path(output_dir).expanduser()
            if not candidate.is_dir():
                raise ValueError(f"输出目录不存在: {output_dir}")
            custom = candidate.resolve()
        path = await self.exporter.export(
            artifact_id=artifact_id,
            title=detail["artifact"]["title"],
            markdown=detail["version"]["markdown"],
            structure=detail["structure"],
            format=format,
            output_dir=custom,
            include_citations=include_citations,
        )
        self.repo.record_export(artifact_id, format, str(path))
        return {"path": str(path), "format": format, "filename": path.name, "artifact_id": artifact_id}

    def latest_export(self, artifact_id: str, format: str) -> Path | None:
        record = self.repo.latest_export(artifact_id, format)
        if record and Path(record["path"]).is_file():
            return Path(record["path"])
        return None


def _title_from_markdown(markdown: str) -> str | None:
    for line in (markdown or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def _short_instruction(instruction: str) -> str:
    text = " ".join((instruction or "").split())
    return text[:40] or "未命名成果"