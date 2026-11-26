"""Internal model contracts shared by every adapter.

The rest of the application talks to these types only; provider-specific
payloads stay inside :mod:`backend.core.models.adapters`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Message = dict[str, Any]
"""OpenAI-style chat message: {"role": ..., "content": ...}."""


@dataclass(slots=True)
class Usage:
    calls: int = 1
    input_tokens: int = 0
    output_tokens: int = 0

    def merge(self, other: Usage) -> Usage:
        return Usage(
            calls=self.calls + other.calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str = "{}"

    def parsed_arguments(self) -> dict[str, Any]:
        try:
            value = json.loads(self.arguments or "{}")
            return value if isinstance(value, dict) else {"value": value}
        except ValueError:
            return {}


@dataclass(slots=True)
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None
    model: str = ""


@dataclass(slots=True)
class EmbeddingResponse:
    vectors: list[list[float]]
    dimensions: int
    usage: Usage = field(default_factory=Usage)
    model: str = ""


@dataclass(slots=True)
class RerankCandidate:
    id: str
    text: str


@dataclass(slots=True)
class RerankHit:
    id: str
    score: float
    index: int


@dataclass(slots=True)
class RerankResponse:
    hits: list[RerankHit]
    usage: Usage = field(default_factory=Usage)
    model: str = ""


@dataclass(slots=True)
class OCRBlock:
    text: str
    bbox: tuple[float, float, float, float] | None = None
    confidence: float | None = None


@dataclass(slots=True)
class OCRResponse:
    text: str
    blocks: list[OCRBlock] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    raw: dict[str, Any] | None = None
    model: str = ""


@dataclass(slots=True)
class EndpointTestResult:
    ok: bool
    kind: str
    endpoint_id: str
    endpoint_name: str
    detail: str = ""
    latency_ms: int | None = None
    dimensions: int | None = None
    tool_calling: bool | None = None
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class ModelError(Exception):
    """Base error for every model invocation problem."""

    code = "model_error"
    retryable = False

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class ModelNotConfigured(ModelError):
    code = "model_not_configured"

    def __init__(self, kind: str) -> None:
        super().__init__(f"尚未配置可用的 {kind} 模型，请先在系统设置中完成配置。")


class ModelAuthError(ModelError):
    code = "model_auth_error"


class ModelRateLimited(ModelError):
    code = "model_rate_limited"
    retryable = True

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ModelTimeout(ModelError):
    code = "model_timeout"
    retryable = True


class ModelUnavailable(ModelError):
    code = "model_unavailable"
    retryable = True


class ModelBadResponse(ModelError):
    code = "model_bad_response"


# --------------------------------------------------------------------------
# Ports
# --------------------------------------------------------------------------


@runtime_checkable
class LLMPort(Protocol):
    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...


@runtime_checkable
class EmbeddingPort(Protocol):
    async def embed(self, texts: list[str], role: Literal["document", "query"]) -> EmbeddingResponse: ...


@runtime_checkable
class RerankPort(Protocol):
    async def rerank(
        self, query: str, candidates: list[RerankCandidate], top_n: int | None = None
    ) -> RerankResponse: ...


@runtime_checkable
class OCRPort(Protocol):
    async def recognize(self, image: bytes, *, mime: str = "image/png") -> OCRResponse: ...


@runtime_checkable
class VLMPort(Protocol):
    async def understand(self, image: bytes, instruction: str, *, mime: str = "image/png") -> LLMResponse: ...


def image_content_message(instruction: str, image_b64: str, mime: str) -> Message:
    """Build an OpenAI-compatible user message carrying one image."""
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": instruction},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
        ],
    }
