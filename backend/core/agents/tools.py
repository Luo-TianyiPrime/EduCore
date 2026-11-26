"""Tool definitions exposed to the document-analysis agent.

All tools are scope-bound: document IDs outside the task scope are rejected.
Returned strings are compact JSON so the model receives structured evidence
without provider-specific tool schemas leaking into our domain layer.
"""

from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path

from pydantic_ai import Agent, RunContext

from backend.core.agents.deps import AgentDeps
from backend.core.documents.pdf import render_page_png
from backend.core.models.types import ModelError, ModelNotConfigured

logger = logging.getLogger(__name__)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def register_tools(agent: Agent) -> None:
    @agent.tool
    def list_files(ctx: RunContext[AgentDeps]) -> str:
        """列出任务范围内的全部文档，包含标题、格式、页数与解析状态。"""
        items = []
        for item in ctx.deps.inputs:
            items.append(
                {
                    "document_id": item["document_id"],
                    "title": item.get("title"),
                    "path": item.get("path"),
                    "format": item.get("format"),
                    "pages": item.get("page_count"),
                    "sections": item.get("section_count"),
                }
            )
        return _json({"documents": items})

    @agent.tool
    def inspect_document(ctx: RunContext[AgentDeps], document_id: str) -> str:
        """读取指定文档的结构概况（页数、文本块数、索引块数、解析状态）。"""
        try:
            document = ctx.deps.document(document_id)
            item = ctx.deps.input_for(document_id)
        except (ValueError, PermissionError) as exc:
            return _json({"error": str(exc)})
        version = ctx.deps.documents.get_version(item["version_id"])
        return _json(
            {
                "document_id": document_id,
                "title": document.get("title"),
                "format": document.get("format"),
                "path": document.get("path"),
                "pages": document.get("page_count"),
                "parse_status": document.get("parse_status"),
                "parse_error": document.get("parse_error"),
                "version": {
                    "id": version["id"] if version else None,
                    "blocks": version["block_count"] if version else 0,
                    "chunks": version["chunk_count"] if version else 0,
                    "vector_status": version["vector_status"] if version else None,
                },
            }
        )

    @agent.tool
    def list_sections(ctx: RunContext[AgentDeps], document_id: str) -> str:
        """列出指定文档的章节结构（标题、层级、页码范围、段落数）。"""
        try:
            item = ctx.deps.input_for(document_id)
        except PermissionError as exc:
            return _json({"error": str(exc)})
        sections = ctx.deps.documents.sections_with_counts(item["version_id"])
        return _json(
            {
                "document_id": document_id,
                "sections": [
                    {
                        "section_id": section["id"],
                        "title": section["title"],
                        "level": section["level"],
                        "page_start": section["page_start"],
                        "page_end": section["page_end"],
                        "blocks": section["block_count"],
                    }
                    for section in sections
                ],
            }
        )

    @agent.tool
    def read_section(
        ctx: RunContext[AgentDeps], document_id: str, section_id: str, max_chars: int = 8000
    ) -> str:
        """读取一个章节的完整正文（按 max_chars 截断），用于精读证据。"""
        try:
            item = ctx.deps.input_for(document_id)
        except PermissionError as exc:
            return _json({"error": str(exc)})
        blocks = ctx.deps.documents.blocks(item["version_id"], section_id)
        if not blocks:
            return _json({"error": "章节不存在或没有正文", "section_id": section_id})
        text = "\n\n".join(block["text"] for block in blocks if block["text"].strip())
        truncated = len(text) > max_chars
        pages = sorted({block["page"] for block in blocks if block["page"]})
        return _json(
            {
                "document_id": document_id,
                "title": ctx.deps.document_title(document_id),
                "section_id": section_id,
                "pages": pages,
                "text": text[:max_chars],
                "truncated": truncated,
            }
        )

    @agent.tool
    def read_pages(
        ctx: RunContext[AgentDeps], document_id: str, pages: list[int], max_chars: int = 10000
    ) -> str:
        """读取指定文档的若干页原文（最多 30 页）。适用于核对页码定位。"""
        try:
            parsed = ctx.deps.snapshot(document_id)
        except (ValueError, PermissionError) as exc:
            return _json({"error": str(exc)})
        requested = sorted({int(page) for page in pages})[:30]
        chunks = []
        total = 0
        for page in requested:
            match = next((item for item in parsed.pages if item.number == page), None)
            if match is None:
                continue
            text = match.text.strip()
            if not text:
                chunks.append(f"（第 {page} 页没有可读文本层）")
                continue
            remaining = max_chars - total
            if remaining <= 0:
                break
            clipped = text[:remaining]
            total += len(clipped)
            chunks.append(f"（第 {page} 页）\n{clipped}")
        return _json(
            {
                "document_id": document_id,
                "requested_pages": requested,
                "text": "\n\n".join(chunks),
                "truncated": total >= max_chars,
            }
        )

    @agent.tool
    async def search_knowledge(
        ctx: RunContext[AgentDeps], query: str, top_k: int = 8
    ) -> str:
        """在任务范围内做混合检索（向量 + 关键词 + 精排），返回可引用的原文片段。"""
        try:
            result = await ctx.deps.retrieval.search(
                query,
                document_ids=sorted(ctx.deps.scope_ids),
                top_k=max(1, min(top_k, 20)),
            )
        except Exception as exc:  # pragma: no cover - provider failure
            return _json({"error": f"{type(exc).__name__}: {exc}"})
        hits = [
            {
                "chunk_id": hit["chunk_id"],
                "document_id": hit["document_id"],
                "document_title": hit["document_title"],
                "section_path": hit["section_path"],
                "page": hit["page_start"],
                "text": hit["text"][:1200],
                "score": hit["score"],
            }
            for hit in result["hits"]
        ]
        return _json({"query": query, "hits": hits, "degraded": result["debug"]["degraded"]})

    @agent.tool
    def scan_documents(ctx: RunContext[AgentDeps]) -> str:
        """查看全量阅读批次的处理状态与已完成批次的分析笔记路径。"""
        if ctx.deps.tasks is None:
            return _json({"error": "任务未初始化批次信息"})
        batches = ctx.deps.tasks.batches(ctx.deps.task_id)
        if not batches:
            return _json(
                {
                    "total": 0,
                    "note": "当前任务没有全量阅读批次；可继续使用检索与章节阅读工具。",
                }
            )
        return _json(
            {
                "total": len(batches),
                "completed": sum(1 for batch in batches if batch["status"] == "completed"),
                "failed": sum(1 for batch in batches if batch["status"] == "failed"),
                "pending": sum(1 for batch in batches if batch["status"] == "pending"),
                "notes": [
                    {
                        "batch_index": batch["batch_index"],
                        "label": batch["label"],
                        "status": batch["status"],
                        "note_path": batch["note_path"],
                    }
                    for batch in batches
                ],
                "note": "笔记文件为 JSON，包含 summary/key_points/evidence/uncertainties。",
            }
        )

    @agent.tool
    async def read_image(
        ctx: RunContext[AgentDeps], document_id: str, page_number: int
    ) -> str:
        """用已配置的视觉模型读取指定页面图像中的图表或图片内容。"""
        try:
            document = ctx.deps.document(document_id)
        except (ValueError, PermissionError) as exc:
            return _json({"error": str(exc)})
        path = Path(document["path"])
        if not path.is_file():
            return _json({"error": "原始文件不存在"})
        try:
            if document["format"] == "pdf":
                image = render_page_png(path, page_number, scale=2.0)
                mime = "image/png"
            elif document["format"] == "image" and page_number == 1:
                image = path.read_bytes()
                mime = "image/png"
            else:
                return _json({"error": "该文档没有页面图像"})
            response = await ctx.deps.gateway.vlm(
                image,
                "请描述这张页面图像中的图表、图片和可见文字，并指出不确定的内容。",
                mime=mime,
            )
        except ModelNotConfigured:
            return _json({"error": "未配置视觉模型（VLM），无法读取图像内容"})
        except ModelError as exc:
            return _json({"error": f"视觉模型调用失败：{exc.message}"})
        except Exception as exc:  # pragma: no cover - rendering failure
            return _json({"error": f"{type(exc).__name__}: {exc}"})
        return _json(
            {
                "document_id": document_id,
                "page": page_number,
                "description": response.text,
                "model": response.model,
            }
        )

    @agent.tool
    async def extract_structured(
        ctx: RunContext[AgentDeps],
        document_id: str,
        section_id: str,
        instruction: str,
    ) -> str:
        """从指定章节中按 instruction 提取结构化 JSON（如试题、知识条目）。"""
        try:
            item = ctx.deps.input_for(document_id)
        except PermissionError as exc:
            return _json({"error": str(exc)})
        blocks = ctx.deps.documents.blocks(item["version_id"], section_id)
        text = "\n\n".join(block["text"] for block in blocks if block["text"].strip())
        if not text:
            return _json({"error": "章节没有正文"})
        messages = [
            {
                "role": "system",
                "content": (
                    "你是资料结构化提取器。只依据给定原文提取，不要补充外部知识；"
                    "无法确定的字段留空并在 _uncertain 中说明。只输出 JSON 对象。"
                ),
            },
            {
                "role": "user",
                "content": f"提取要求：{instruction}\n\n原文（{ctx.deps.document_title(document_id)}）：\n{text[:14000]}",
            },
        ]
        try:
            response = await ctx.deps.gateway.llm(
                messages, schema={"type": "object"}, temperature=0
            )
        except ModelError as exc:
            return _json({"error": f"提取失败：{exc.message}"})
        return response.text or _json({"error": "模型返回空结果"})

    @agent.tool
    async def compare_sources(
        ctx: RunContext[AgentDeps], topic: str, top_k: int = 10
    ) -> str:
        """按主题在多个来源中检索，并按文档分组返回表述与来源，便于对照差异。"""
        try:
            result = await ctx.deps.retrieval.search(
                topic, document_ids=sorted(ctx.deps.scope_ids), top_k=max(2, min(top_k, 20))
            )
        except Exception as exc:  # pragma: no cover
            return _json({"error": f"{type(exc).__name__}: {exc}"})
        grouped: dict[str, list[dict]] = {}
        for hit in result["hits"]:
            grouped.setdefault(hit["document_title"] or hit["document_id"], []).append(
                {
                    "page": hit["page_start"],
                    "section": " / ".join(hit["section_path"]),
                    "text": hit["text"][:800],
                }
            )
        return _json({"topic": topic, "sources": grouped})

    @agent.tool
    def compute_statistics(ctx: RunContext[AgentDeps], values: list[float]) -> str:
        """由程序计算一组数值的数量、总分、均值、中位数、最小值和最大值。"""
        numbers = [float(value) for value in values if isinstance(value, (int, float))]
        if not numbers:
            return _json({"error": "没有可计算的数值"})
        return _json(
            {
                "count": len(numbers),
                "sum": sum(numbers),
                "mean": statistics.fmean(numbers),
                "median": statistics.median(numbers),
                "min": min(numbers),
                "max": max(numbers),
            }
        )

    @agent.tool
    def write_draft(ctx: RunContext[AgentDeps], markdown: str) -> str:
        """将当前成果写入任务的 Markdown 草稿文件，供用户预览与后续导出。"""
        output_dir = ctx.deps.task_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "draft.md"
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(markdown, encoding="utf-8")
        tmp.replace(path)
        ctx.deps.events.publish(
            ctx.deps.task_id,
            "artifact",
            {"path": str(path), "chars": len(markdown)},
        )
        return _json({"saved": str(path), "chars": len(markdown)})