"""Model gateway and provider adapters."""

from backend.core.models.gateway import ModelGateway, model_task_scope
from backend.core.models.types import (
    EmbeddingResponse,
    EndpointTestResult,
    LLMResponse,
    Message,
    ModelError,
    ModelNotConfigured,
    OCRResponse,
    RerankCandidate,
    RerankHit,
    RerankResponse,
    ToolCall,
    ToolSpec,
    Usage,
)

__all__ = [
    "EmbeddingResponse",
    "EndpointTestResult",
    "LLMResponse",
    "Message",
    "ModelError",
    "ModelGateway",
    "ModelNotConfigured",
    "OCRResponse",
    "RerankCandidate",
    "RerankHit",
    "RerankResponse",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "model_task_scope",
]