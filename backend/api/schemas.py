"""API request/response schemas.

API keys are write-only: requests may carry ``api_key``, responses only expose
``has_api_key``.  Keys are stored by :mod:`backend.platform.secrets`, never in
the settings file.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.core.config import EndpointConfig, EndpointKind, Provider


class EndpointIn(EndpointConfig):
    """Endpoint payload accepted from the settings UI."""

    api_key: str | None = Field(default=None, exclude=True)
    clear_api_key: bool = Field(default=False, exclude=True)


class EndpointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: EndpointKind
    provider: Provider
    base_url: str
    model: str
    has_api_key: bool = False
    timeout_s: float
    concurrency: int
    rpm: int | None = None
    tpm: int | None = None
    context_limit: int | None = None
    dimensions: int | None = None
    query_instruction: str | None = None
    document_instruction: str | None = None
    distance: str = "cosine"
    batch_size: int = 32
    price_input_per_mtok: float | None = None
    price_output_per_mtok: float | None = None
    price_per_call: float | None = None
    enabled: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_config(cls, endpoint: EndpointConfig, has_api_key: bool) -> EndpointOut:
        return cls(
            id=endpoint.id,
            name=endpoint.name,
            kind=endpoint.kind,
            provider=endpoint.provider,
            base_url=endpoint.base_url,
            model=endpoint.model,
            has_api_key=has_api_key,
            timeout_s=endpoint.timeout_s,
            concurrency=endpoint.concurrency,
            rpm=endpoint.rpm,
            tpm=endpoint.tpm,
            context_limit=endpoint.context_limit,
            dimensions=endpoint.dimensions,
            query_instruction=endpoint.query_instruction,
            document_instruction=endpoint.document_instruction,
            distance=endpoint.distance,
            batch_size=endpoint.batch_size,
            price_input_per_mtok=endpoint.price_input_per_mtok,
            price_output_per_mtok=endpoint.price_output_per_mtok,
            price_per_call=endpoint.price_per_call,
            enabled=endpoint.enabled,
            extra=endpoint.extra,
        )


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: str | None = None
    embedding: str | None = None
    reranker: str | None = None
    ocr: str | None = None
    vlm: str | None = None


class ModelsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoints: list[EndpointIn]
    defaults: Defaults = Field(default_factory=Defaults)


class ModelsResponse(BaseModel):
    endpoints: list[EndpointOut]
    defaults: Defaults
    secret_backend: str


class TestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint_id: str | None = None
    endpoint: EndpointIn | None = None
    image_base64: str | None = None


class SettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str | None = None
    retrieval: dict[str, Any] | None = None
    chunking: dict[str, Any] | None = None
    max_task_steps: int | None = None
    task_budget_usd: float | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    data_dir: str
    database: bool

class SourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    recursive: bool = True
    scan: bool = True


class DocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str | None = None
    document_id: str | None = None
    force: bool = False


class OcrPagesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pages: list[int] = Field(default_factory=list)


class PickPathRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "directory"


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    document_ids: list[str] | None = None
    source_ids: list[str] | None = None
    top_k: int | None = None
    use_reranker: bool = True


class VectorRebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str | None = None
    document_id: str | None = None


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str
    document_ids: list[str] | None = None
    source_ids: list[str] | None = None
    mode: Literal["auto", "precise", "full", "mixed"] = "auto"


class TaskActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["pause", "resume", "cancel", "retry"]


class ArtifactUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markdown: str


class ArtifactExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["md", "docx", "json", "pdf"]
    output_dir: str | None = None
    include_citations: bool = True


class ArtifactRegenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str
    instruction: str
