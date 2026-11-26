"""Full-document reading engine: batch analysis, caching and synthesis."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.core.agents.runner import batch_messages
from backend.core.config import AppConfig, EndpointConfig
from backend.core.documents.types import ParsedDocument
from backend.core.models.adapters.openai_compat import parse_json_text
from backend.core.models.gateway import ModelGateway
from backend.core.prompts import load_prompt
from backend.platform.paths import DataPaths
from backend.storage.tasks import TaskRepo

logger = logging.getLogger(__name__)


class FullReadEngine:
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        tasks: TaskRepo,
        paths: DataPaths,
        config_provider: Callable[[], AppConfig],
    ) -> None:
        self.gateway = gateway
        self.tasks = tasks
        self.paths = paths
        self.config_provider = config_provider

    # -- notes -------------------------------------------------------------
    async def analyze_batch(
        self,
        *,
        task_id: str,
        instruction: str,
        batch: dict[str, Any],
        parsed: ParsedDocument,
        document_title: str,
        endpoint: EndpointConfig,
        text: str,
    ) -> dict[str, Any]:
        cache_key = _cache_key(
            [
                instruction,
                endpoint.model,
                str(batch["document_id"]),
                json.dumps(batch["segments"], sort_keys=True),
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
            ],
            purpose="full_batch",
        )
        cached = self.tasks.cache_get(cache_key)
        if cached is not None:
            try:
                note = json.loads(cached["output"])
                note["cached"] = True
                return note
            except ValueError:
                pass

        messages = batch_messages(instruction, document_title, batch.get("label") or "", text)
        started = time.perf_counter()
        response = await self.gateway.llm(
            messages,
            endpoint_id=endpoint.id,
            schema={"type": "object"},
            temperature=0.2,
            max_tokens=2000,
        )
        parsed_json = parse_json_text(response.text)
        if isinstance(parsed_json, dict):
            note = {
                "summary": str(parsed_json.get("summary") or "").strip(),
                "key_points": parsed_json.get("key_points") or [],
                "evidence": parsed_json.get("evidence") or [],
                "uncertainties": parsed_json.get("uncertainties") or [],
            }
        else:
            note = {
                "summary": response.text.strip()[:2000],
                "key_points": [],
                "evidence": [],
                "uncertainties": ["模型未返回约定的 JSON 结构，已保留原始文本摘要"],
            }
        note.update(
            {
                "batch_index": batch["batch_index"],
                "batch_id": batch["id"],
                "document_id": batch["document_id"],
                "document_title": document_title,
                "label": batch.get("label"),
                "page_start": batch.get("page_start"),
                "page_end": batch.get("page_end"),
                "model": response.model or endpoint.model,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "cached": False,
            }
        )
        self.tasks.cache_put(
            key=cache_key,
            input_hash=cache_key,
            purpose="full_batch",
            model=endpoint.model,
            output=json.dumps(note, ensure_ascii=False),
            task_id=task_id,
        )
        return note

    def save_note(self, task_id: str, note: dict[str, Any]) -> Path:
        directory = self.paths.tasks_dir / task_id / "notes"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"batch_{int(note['batch_index']):04d}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(note, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return path

    # -- synthesis ---------------------------------------------------------
    async def synthesize(
        self,
        *,
        task_id: str,
        instruction: str,
        scope_inputs: list[dict[str, Any]],
        notes: list[dict[str, Any]],
        endpoint: EndpointConfig,
        extra_evidence: str = "",
    ) -> str:
        context_limit = endpoint.context_limit or 16000
        budget_chars = max(12000, int(context_limit * 2.2))
        notes_block = json.dumps(notes, ensure_ascii=False)
        if len(notes_block) > budget_chars:
            notes_block = await self._merge_notes(notes, endpoint, budget_chars)

        scope_lines = "\n".join(
            f"- {item.get('title')}（{item.get('page_count')} 页，{item.get('path')}）"
            for item in scope_inputs
        )
        user_content = (
            f"用户任务：\n{instruction}\n\n"
            f"资料范围：\n{scope_lines}\n\n"
            f"批次分析笔记：\n{notes_block}"
        )
        if extra_evidence:
            user_content += f"\n\n补充检索证据（用于交叉核对）：\n{extra_evidence}"

        response = await self.gateway.llm(
            [
                {"role": "system", "content": load_prompt("synthesis.md")},
                {"role": "user", "content": user_content},
            ],
            endpoint_id=endpoint.id,
            temperature=0.25,
            max_tokens=8000,
        )
        text = response.text.strip()
        if not text:
            raise ValueError("综合阶段模型返回空内容")
        return text

    async def _merge_notes(
        self, notes: list[dict[str, Any]], endpoint: EndpointConfig, budget_chars: int
    ) -> str:
        groups: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_chars = 0
        for note in notes:
            size = len(json.dumps(note, ensure_ascii=False))
            if current and current_chars + size > budget_chars // 2:
                groups.append(current)
                current = []
                current_chars = 0
            current.append(note)
            current_chars += size
        if current:
            groups.append(current)

        merged: list[str] = []
        for index, group in enumerate(groups, start=1):
            response = await self.gateway.llm(
                [
                    {
                        "role": "system",
                        "content": (
                            "把下列批次笔记压缩为精炼的中文要点清单。保留可核对的原文引文、页码、"
                            "来源文档与不确定性；删除重复表述。不要添加笔记中没有的信息。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(group, ensure_ascii=False),
                    },
                ],
                endpoint_id=endpoint.id,
                temperature=0.2,
                max_tokens=2500,
            )
            merged.append(f"### 笔记组 {index}\n{response.text.strip()}")
        return "\n\n".join(merged)


def _cache_key(parts: list[str], *, purpose: str) -> str:
    payload = "\x1f".join([purpose, *parts])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]