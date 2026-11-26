"""Application configuration models and persistence.

Only non-secret settings live here.  API keys are referenced by ``api_key_ref``
and stored by :mod:`backend.platform.secrets`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

EndpointKind = Literal["llm", "embedding", "reranker", "ocr", "vlm"]
Provider = Literal["openai_compat", "rerank_http", "ocr_http", "ocr_cli"]
Distance = Literal["cosine", "l2", "ip"]

PROVIDERS_BY_KIND: dict[str, set[str]] = {
    "llm": {"openai_compat"},
    "embedding": {"openai_compat"},
    "reranker": {"rerank_http"},
    "ocr": {"ocr_http", "ocr_cli"},
    "vlm": {"openai_compat"},
}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class EndpointConfig(BaseModel):
    """A single model service endpoint (LLM, embedding, reranker, OCR, VLM)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_id)
    name: str = ""
    kind: EndpointKind
    provider: Provider
    base_url: str = ""
    model: str = ""
    api_key_ref: str | None = None

    timeout_s: float = 60.0
    concurrency: int = 4
    rpm: int | None = None
    tpm: int | None = None
    context_limit: int | None = None

    # Embedding-specific
    dimensions: int | None = None
    query_instruction: str | None = None
    document_instruction: str | None = None
    distance: Distance = "cosine"
    batch_size: int = 32

    # Cost accounting (optional; never invent costs when unknown)
    price_input_per_mtok: float | None = None
    price_output_per_mtok: float | None = None
    price_per_call: float | None = None

    enabled: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", mode="before")
    @classmethod
    def _default_name(cls, value: str | None, info) -> str:
        return value or ""

    @model_validator(mode="after")
    def _check_provider(self) -> EndpointConfig:
        allowed = PROVIDERS_BY_KIND.get(self.kind)
        if allowed is not None and self.provider not in allowed:
            raise ValueError(f"{self.kind} 不支持 provider={self.provider}，可选: {sorted(allowed)}")
        if self.provider == "ocr_cli" and not self.extra.get("command"):
            raise ValueError("本地 CLI OCR 必须提供 extra.command")
        if self.provider != "ocr_cli" and not self.base_url:
            raise ValueError("base_url 不能为空")
        if self.kind in {"llm", "embedding", "vlm", "reranker"} and not self.model:
            raise ValueError("model 不能为空")
        if not self.name:
            self.name = self.model or (self.base_url.rstrip("/").split("/")[-1] if self.base_url else self.id)
        if self.kind == "embedding" and self.dimensions is not None and self.dimensions <= 0:
            raise ValueError("dimensions 必须为正整数")
        if self.concurrency < 1:
            raise ValueError("concurrency 至少为 1")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s 必须大于 0")
        return self

    @property
    def is_configured(self) -> bool:
        """Whether the endpoint has the minimum fields needed to be called."""
        if not self.enabled:
            return False
        if self.provider == "ocr_cli":
            return bool(self.extra.get("command"))
        return bool(self.base_url)


class RetrievalSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vector_candidates: int = 100
    bm25_candidates: int = 100
    fusion_top: int = 60
    final_top: int = 15
    rrf_k: int = 60
    expand_parent: bool = True
    expand_neighbors: int = 1


class ChunkingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_tokens: int = 384
    max_tokens: int = 512
    min_tokens: int = 64
    parent_target_tokens: int = 2048
    parent_max_tokens: int = 4096
    overlap_tokens: int = 0


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    endpoints: list[EndpointConfig] = Field(default_factory=list)

    default_llm: str | None = None
    default_embedding: str | None = None
    default_reranker: str | None = None
    default_ocr: str | None = None
    default_vlm: str | None = None

    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)

    theme: Literal["light", "dark", "system"] = "light"
    language: str = "zh-CN"
    max_task_steps: int = 120
    task_budget_usd: float | None = None

    @model_validator(mode="after")
    def _check_unique(self) -> AppConfig:
        seen: set[str] = set()
        for endpoint in self.endpoints:
            if endpoint.id in seen:
                raise ValueError(f"endpoint id 重复: {endpoint.id}")
            seen.add(endpoint.id)
        for field in ("default_llm", "default_embedding", "default_reranker", "default_ocr", "default_vlm"):
            value = getattr(self, field)
            if value and value not in seen:
                setattr(self, field, None)
        return self

    def endpoint(self, endpoint_id: str) -> EndpointConfig | None:
        return next((e for e in self.endpoints if e.id == endpoint_id), None)

    def default_endpoint(self, kind: EndpointKind) -> EndpointConfig | None:
        field = {
            "llm": "default_llm",
            "embedding": "default_embedding",
            "reranker": "default_reranker",
            "ocr": "default_ocr",
            "vlm": "default_vlm",
        }[kind]
        endpoint_id = getattr(self, field)
        if endpoint_id:
            endpoint = self.endpoint(endpoint_id)
            if endpoint and endpoint.kind == kind and endpoint.is_configured:
                return endpoint
        # Fall back to the first usable endpoint of the requested kind.
        return next(
            (e for e in self.endpoints if e.kind == kind and e.is_configured),
            None,
        )

    def endpoints_of_kind(self, kind: EndpointKind) -> list[EndpointConfig]:
        return [e for e in self.endpoints if e.kind == kind and e.is_configured]


class ConfigStore:
    """Load/save :class:`AppConfig` as JSON with atomic replacement."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> AppConfig:
        if not self.path.is_file():
            return AppConfig()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return AppConfig.model_validate(payload)
        except (OSError, ValueError) as exc:
            # Keep the broken file for inspection instead of silently deleting data.
            backup = self.path.with_name(
                f"{self.path.stem}.corrupt-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.json"
            )
            with contextlib.suppress(OSError):
                shutil.copy2(self.path, backup)
            logger.error("配置文件无法解析，已备份到 %s 并使用默认配置: %s", backup, exc)
            return AppConfig()

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)