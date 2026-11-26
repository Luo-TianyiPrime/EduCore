"""Adapters for providers speaking the OpenAI wire protocol."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Literal

from backend.core.models.adapters.base import HttpAdapterBase, dump_short
from backend.core.models.types import (
    EmbeddingResponse,
    LLMResponse,
    Message,
    ModelBadResponse,
    ToolCall,
    ToolSpec,
    Usage,
    image_content_message,
)

logger = logging.getLogger(__name__)


class OpenAICompatLLM(HttpAdapterBase):
    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.endpoint.model,
            "messages": messages,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = [tool.to_openai() for tool in tools]
            payload["tool_choice"] = "auto"
        if schema is not None and self.endpoint.extra.get("json_mode", True):
            payload["response_format"] = {"type": "json_object"}

        data = await self.post_json(self.resolve_url("chat/completions"), payload)

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelBadResponse(
                f"{self.endpoint.name} 未返回 choices", detail=dump_short(data)
            )
        message = choices[0].get("message") or {}
        text = message.get("content") or ""
        if isinstance(text, list):
            # Some compatible servers return content parts even for text.
            text = "".join(
                part.get("text", "")
                for part in text
                if isinstance(part, dict) and part.get("type") in (None, "text")
            )

        tool_calls: list[ToolCall] = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            name = function.get("name")
            if not name:
                continue
            tool_calls.append(
                ToolCall(
                    id=str(item.get("id") or f"call_{len(tool_calls)}"),
                    name=str(name),
                    arguments=function.get("arguments") or "{}",
                )
            )

        usage_block = data.get("usage") or {}
        usage = Usage(
            calls=1,
            input_tokens=int(usage_block.get("prompt_tokens") or 0),
            output_tokens=int(usage_block.get("completion_tokens") or 0),
        )
        if usage.input_tokens == 0 and usage.output_tokens == 0:
            usage = Usage(calls=1)

        return LLMResponse(
            text=str(text),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=choices[0].get("finish_reason"),
            model=str(data.get("model") or self.endpoint.model),
        )

    async def understand(self, image: bytes, instruction: str, *, mime: str = "image/png") -> LLMResponse:
        image_b64 = base64.b64encode(image).decode("ascii")
        message = image_content_message(instruction, image_b64, mime)
        system = self.endpoint.extra.get("vlm_system_prompt")
        messages: list[Message] = []
        if system:
            messages.append({"role": "system", "content": str(system)})
        messages.append(message)
        return await self.generate(messages)


class OpenAICompatEmbedding(HttpAdapterBase):
    async def embed(
        self, texts: list[str], role: Literal["document", "query"] = "document"
    ) -> EmbeddingResponse:
        if not texts:
            return EmbeddingResponse(vectors=[], dimensions=0)
        payload: dict[str, Any] = {"model": self.endpoint.model, "input": texts}
        if self.endpoint.dimensions and self.endpoint.extra.get("send_dimensions"):
            payload["dimensions"] = self.endpoint.dimensions

        data = await self.post_json(self.resolve_url("embeddings"), payload)

        items = data.get("data")
        if not isinstance(items, list) or not items:
            raise ModelBadResponse(
                f"{self.endpoint.name} 未返回向量数据", detail=dump_short(data)
            )
        ordered = sorted(items, key=lambda item: int(item.get("index", 0)))
        vectors: list[list[float]] = []
        for item in ordered:
            embedding = item.get("embedding")
            if not isinstance(embedding, list) or not embedding:
                raise ModelBadResponse(
                    f"{self.endpoint.name} 返回的向量为空", detail=dump_short(item)
                )
            vectors.append([float(value) for value in embedding])
        if len(vectors) != len(texts):
            raise ModelBadResponse(
                f"{self.endpoint.name} 返回向量数量不匹配：请求 {len(texts)}，返回 {len(vectors)}"
            )
        dimensions = len(vectors[0])
        dimensions_seen = {len(vector) for vector in vectors}
        if len(dimensions_seen) != 1:
            raise ModelBadResponse(
                f"{self.endpoint.name} 返回的向量维度不一致: {sorted(dimensions_seen)}"
            )
        if self.endpoint.dimensions and dimensions != self.endpoint.dimensions:
            raise ModelBadResponse(
                f"{self.endpoint.name} 维度不符：配置 {self.endpoint.dimensions}，实际 {dimensions}"
            )

        usage_block = data.get("usage") or {}
        usage = Usage(
            calls=1,
            input_tokens=int(usage_block.get("prompt_tokens") or 0),
            output_tokens=0,
        )
        return EmbeddingResponse(
            vectors=vectors,
            dimensions=dimensions,
            usage=usage,
            model=str(data.get("model") or self.endpoint.model),
        )


def encode_image(image: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"


def parse_json_text(text: str) -> Any:
    """Best-effort JSON extraction used by structured-output callers."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lstrip().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    try:
        return json.loads(cleaned)
    except ValueError:
        return None