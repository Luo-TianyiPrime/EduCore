"""Dependencies available to the document-analysis agent tools."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.core.documents.types import ParsedDocument, document_from_dict
from backend.core.models.gateway import ModelGateway
from backend.core.retrieval.pipeline import RetrievalService
from backend.core.tasks.events import EventBus
from backend.storage.documents import DocumentRepo

logger = logging.getLogger(__name__)


@dataclass
class AgentDeps:
    gateway: ModelGateway
    documents: DocumentRepo
    retrieval: RetrievalService
    events: EventBus
    task_id: str
    task_dir: Path
    inputs: list[dict[str, Any]]
    tasks: Any = None
    _documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    _snapshots: dict[str, ParsedDocument] = field(default_factory=dict)

    @property
    def scope_ids(self) -> set[str]:
        return {item["document_id"] for item in self.inputs}

    def input_for(self, document_id: str) -> dict[str, Any]:
        for item in self.inputs:
            if item["document_id"] == document_id:
                return item
        raise PermissionError(f"文档不在任务范围内: {document_id}")

    def document(self, document_id: str) -> dict[str, Any]:
        self.input_for(document_id)
        cached = self._documents.get(document_id)
        if cached is not None:
            return cached
        row = self.documents.get(document_id)
        if row is None:
            raise ValueError("文档记录不存在")
        self._documents[document_id] = row
        return row

    def snapshot(self, document_id: str) -> ParsedDocument:
        cached = self._snapshots.get(document_id)
        if cached is not None:
            return cached
        item = self.input_for(document_id)
        version = self.documents.get_version(item["version_id"])
        if version is None or not version.get("parsed_path"):
            raise ValueError("该文档缺少解析快照，请重新索引")
        path = Path(version["parsed_path"])
        if not path.is_file():
            raise ValueError("解析快照文件缺失，请重新索引")
        parsed = document_from_dict(json.loads(path.read_text(encoding="utf-8")))
        self._snapshots[document_id] = parsed
        return parsed

    def document_title(self, document_id: str) -> str:
        try:
            item = self.input_for(document_id)
        except PermissionError:
            return document_id
        return item.get("title") or item.get("path") or document_id