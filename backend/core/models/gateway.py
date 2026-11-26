"""Model gateway: endpoint resolution, adapters, retries, limits and usage.

Every feature module calls the gateway instead of talking to a provider
directly.  The gateway is deliberately strict about missing configuration:
calls raise :class:`ModelNotConfigured` rather than silently falling back to a
different service the user did not authorise.
"""

from __future__ import annotations

import asyncio
import io
import logging
import secrets as _secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import httpx

from backend.core.config import AppConfig, EndpointConfig
from backend.core.models.adapters.ocr_http import CLIOCRAdapter, OCRHttpAdapter
from backend.core.models.adapters.openai_compat import OpenAICompatEmbedding, OpenAICompatLLM
from backend.core.models.adapters.rerank import RerankHTTP
from backend.core.models.types import (
    EmbeddingResponse,
    EndpointTestResult,
    LLMResponse,
    Message,
    ModelAuthError,
    ModelBadResponse,
    ModelError,
    ModelNotConfigured,
    ModelUnavailable,
    OCRResponse,
    RerankCandidate,
    RerankResponse,
    ToolSpec,
    Usage,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

_current_task_id: ContextVar[str | None] = ContextVar("educore_model_task_id", default=None)


@contextmanager
def model_task_scope(task_id: str | None):
    """Attribute model usage to a task for the duration of the block."""
    token = _current_task_id.set(task_id)
    try:
        yield
    finally:
        _current_task_id.reset(token)


def current_task_id() -> str | None:
    return _current_task_id.get()


class _RateLimiter:
    """Sliding-window requests-per-minute limiter."""

    def __init__(self, rpm: int | None) -> None:
        self.rpm = rpm
        self._times: deque[float] = deque()

    async def acquire(self) -> None:
        if not self.rpm or self.rpm <= 0:
            return
        while True:
            now = time.monotonic()
            while self._times and now - self._times[0] >= 60.0:
                self._times.popleft()
            if len(self._times) < self.rpm:
                self._times.append(now)
                return
            wait = 60.0 - (now - self._times[0]) + 0.01
            await asyncio.sleep(max(wait, 0.01))


@dataclass
class _Runtime:
    endpoint: EndpointConfig
    adapter: Any
    semaphore: asyncio.Semaphore
    limiter: _RateLimiter = field(default_factory=lambda: _RateLimiter(None))


def estimate_cost(endpoint: EndpointConfig, usage: Usage) -> float | None:
    """Return an estimated USD cost, or ``None`` when no price is configured."""
    known = False
    cost = 0.0
    if endpoint.price_input_per_mtok is not None:
        cost += usage.input_tokens / 1_000_000 * endpoint.price_input_per_mtok
        known = True
    if endpoint.price_output_per_mtok is not None:
        cost += usage.output_tokens / 1_000_000 * endpoint.price_output_per_mtok
        known = True
    if endpoint.price_per_call is not None:
        cost += usage.calls * endpoint.price_per_call
        known = True
    return cost if known else None


def make_probe_image() -> bytes | None:
    """Create a small text image for OCR/VLM connectivity checks."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    image = Image.new("RGB", (720, 200), "white")
    draw = ImageDraw.Draw(image)
    font = None
    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            try:
                font = ImageFont.truetype(candidate, 56)
                break
            except OSError:
                continue
    if font is None:
        font = ImageFont.load_default()
    draw.text((40, 60), "EDUCORE 2026", fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class ModelGateway:
    def __init__(
        self,
        config: AppConfig,
        secrets: Any,
        db: Any | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._secrets = secrets
        self._db = db
        self._runtimes: dict[str, _Runtime] = {}
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            timeout=httpx.Timeout(60.0, connect=15.0),
        )
        self._max_attempts = 3

    # -- lifecycle ---------------------------------------------------------
    def reconfigure(self, config: AppConfig) -> None:
        self._config = config
        self._runtimes.clear()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- resolution --------------------------------------------------------
    def resolve(self, kind: str, endpoint_id: str | None = None) -> EndpointConfig:
        if endpoint_id:
            endpoint = self._config.endpoint(endpoint_id)
            if endpoint is None or endpoint.kind != kind:
                raise ModelNotConfigured(kind)
        else:
            endpoint = self._config.default_endpoint(kind)  # type: ignore[arg-type]
        if endpoint is None or not endpoint.is_configured:
            raise ModelNotConfigured(kind)
        return endpoint

    def api_key(self, endpoint: EndpointConfig) -> str | None:
        if not endpoint.api_key_ref:
            return None
        try:
            return self._secrets.get(endpoint.api_key_ref)
        except Exception as exc:  # pragma: no cover - host specific
            logger.warning("读取 API Key 失败: %s", exc)
            return None

    def _build_adapter(self, endpoint: EndpointConfig) -> Any:
        api_key = self.api_key(endpoint)
        if endpoint.provider == "openai_compat":
            if endpoint.kind == "embedding":
                return OpenAICompatEmbedding(self._client, endpoint, api_key)
            return OpenAICompatLLM(self._client, endpoint, api_key)
        if endpoint.provider == "rerank_http":
            return RerankHTTP(self._client, endpoint, api_key)
        if endpoint.provider == "ocr_http":
            return OCRHttpAdapter(self._client, endpoint, api_key)
        if endpoint.provider == "ocr_cli":
            return CLIOCRAdapter(endpoint)
        raise ModelNotConfigured(endpoint.kind)

    def _runtime(self, endpoint: EndpointConfig) -> _Runtime:
        runtime = self._runtimes.get(endpoint.id)
        if runtime is None or runtime.endpoint is not endpoint:
            runtime = _Runtime(
                endpoint=endpoint,
                adapter=self._build_adapter(endpoint),
                semaphore=asyncio.Semaphore(max(1, endpoint.concurrency)),
                limiter=_RateLimiter(endpoint.rpm),
            )
            self._runtimes[endpoint.id] = runtime
        return runtime

    # -- invocation --------------------------------------------------------
    async def _invoke(self, endpoint: EndpointConfig, call: Callable[[Any], Awaitable[T]]) -> T:
        runtime = self._runtime(endpoint)
        last_error: ModelError | None = None
        for attempt in range(self._max_attempts):
            await runtime.limiter.acquire()
            try:
                async with runtime.semaphore:
                    return await call(runtime.adapter)
            except ModelError as exc:
                last_error = exc
                if not exc.retryable or attempt == self._max_attempts - 1:
                    raise
                delay = getattr(exc, "retry_after", None) or (1.2**attempt + _secrets.randbelow(300) / 1000)
                logger.warning(
                    "%s 调用失败（%s），%.1fs 后重试 (%d/%d)",
                    endpoint.name,
                    exc.code,
                    delay,
                    attempt + 1,
                    self._max_attempts,
                )
                await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error

    def _record(self, endpoint: EndpointConfig, usage: Usage) -> None:
        if self._db is None or usage.calls <= 0:
            return
        try:
            self._db.record_usage(
                endpoint_id=endpoint.id,
                endpoint_kind=endpoint.kind,
                model=endpoint.model or None,
                task_id=current_task_id(),
                calls=usage.calls,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_estimate=estimate_cost(endpoint, usage),
            )
        except Exception as exc:  # pragma: no cover - usage must never break a call
            logger.warning("写入模型用量失败: %s", exc)

    # -- public API --------------------------------------------------------
    async def llm(
        self,
        messages: list[Message],
        *,
        endpoint_id: str | None = None,
        tools: list[ToolSpec] | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        endpoint = self.resolve("llm", endpoint_id)
        response = await self._invoke(
            endpoint,
            lambda adapter: adapter.generate(
                messages,
                tools=tools,
                schema=schema,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )
        self._record(endpoint, response.usage)
        return response

    async def embed(
        self,
        texts: list[str],
        *,
        endpoint_id: str | None = None,
        role: str = "document",
    ) -> EmbeddingResponse:
        endpoint = self.resolve("embedding", endpoint_id)
        prefix = (
            endpoint.query_instruction
            if role == "query"
            else endpoint.document_instruction
        ) or ""
        prepared = [f"{prefix}{text}" for text in texts] if prefix else list(texts)

        batch_size = max(1, endpoint.batch_size)
        vectors: list[list[float]] = []
        dimensions = endpoint.dimensions or 0
        total = Usage(calls=0)
        model = endpoint.model
        for start in range(0, len(prepared), batch_size):
            batch = prepared[start : start + batch_size]
            response = await self._invoke(
                endpoint, lambda adapter, batch=batch: adapter.embed(batch, role=role)
            )
            vectors.extend(response.vectors)
            dimensions = response.dimensions
            total = total.merge(response.usage)
            model = response.model or model
            self._record(endpoint, response.usage)
        if not vectors:
            return EmbeddingResponse(vectors=[], dimensions=dimensions, model=model)
        return EmbeddingResponse(vectors=vectors, dimensions=dimensions, usage=total, model=model)

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        *,
        endpoint_id: str | None = None,
        top_n: int | None = None,
    ) -> RerankResponse:
        endpoint = self.resolve("reranker", endpoint_id)
        response = await self._invoke(
            endpoint,
            lambda adapter: adapter.rerank(query, candidates, top_n=top_n),
        )
        self._record(endpoint, response.usage)
        return response

    async def ocr(
        self,
        image: bytes,
        *,
        endpoint_id: str | None = None,
        mime: str = "image/png",
    ) -> OCRResponse:
        endpoint = self.resolve("ocr", endpoint_id)
        response = await self._invoke(
            endpoint, lambda adapter: adapter.recognize(image, mime=mime)
        )
        self._record(endpoint, response.usage)
        return response

    async def vlm(
        self,
        image: bytes,
        instruction: str,
        *,
        endpoint_id: str | None = None,
        mime: str = "image/png",
    ) -> LLMResponse:
        endpoint = self.resolve("vlm", endpoint_id)
        response = await self._invoke(
            endpoint, lambda adapter: adapter.understand(image, instruction, mime=mime)
        )
        self._record(endpoint, response.usage)
        return response

    # -- diagnostics -------------------------------------------------------
    async def test_endpoint(
        self,
        endpoint: EndpointConfig,
        *,
        image: bytes | None = None,
    ) -> EndpointTestResult:
        started = time.perf_counter()
        result = EndpointTestResult(
            ok=False,
            kind=endpoint.kind,
            endpoint_id=endpoint.id,
            endpoint_name=endpoint.name,
        )
        if not endpoint.is_configured:
            result.detail = "配置不完整（缺少 base_url / model / command）"
            return result

        try:
            if endpoint.kind == "llm":
                await self._test_llm(endpoint, result)
            elif endpoint.kind == "embedding":
                await self._test_embedding(endpoint, result)
            elif endpoint.kind == "reranker":
                await self._test_reranker(endpoint, result)
            elif endpoint.kind == "ocr":
                await self._test_ocr(endpoint, result, image)
            elif endpoint.kind == "vlm":
                await self._test_vlm(endpoint, result, image)
            else:
                result.detail = f"未知端点类型: {endpoint.kind}"
        except ModelError as exc:
            result.ok = False
            result.detail = f"[{exc.code}] {exc.message}"
            if exc.detail:
                result.detail += f" | {exc.detail[:300]}"
        except Exception as exc:  # pragma: no cover - defensive
            result.ok = False
            result.detail = f"未预期错误: {type(exc).__name__}: {exc}"
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    async def _test_llm(self, endpoint: EndpointConfig, result: EndpointTestResult) -> None:
        tool = ToolSpec(
            name="ping",
            description="Connectivity probe. Call it with reason='connectivity test'.",
            parameters={
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        )
        response = await self._invoke(
            endpoint,
            lambda adapter: adapter.generate(
                [
                    {
                        "role": "user",
                        "content": "这是连接测试。请调用 ping 工具，reason 填写 connectivity test。",
                    }
                ],
                tools=[tool],
                temperature=0,
            ),
        )
        self._record(endpoint, response.usage)
        if response.tool_calls:
            result.ok = True
            result.tool_calling = True
            result.detail = f"文本与工具调用均正常（tool={response.tool_calls[0].name}）"
            return
        # Fall back to a plain completion so text-only models can still be used.
        fallback = await self._invoke(
            endpoint,
            lambda adapter: adapter.generate(
                [{"role": "user", "content": "请只回复两个字：正常"}], temperature=0
            ),
        )
        self._record(endpoint, fallback.usage)
        if fallback.text.strip():
            result.ok = True
            result.tool_calling = False
            result.detail = "文本生成正常，但模型未返回工具调用"
            result.warnings.append("该模型可能不支持工具调用，Agent 功能会受限")
        else:
            result.detail = "模型返回空内容"

    async def _test_embedding(self, endpoint: EndpointConfig, result: EndpointTestResult) -> None:
        response = await self._invoke(
            endpoint,
            lambda adapter: adapter.embed(["连通性测试", "connectivity test"], role="document"),
        )
        self._record(endpoint, response.usage)
        if len(response.vectors) != 2:
            result.detail = f"返回向量数量异常: {len(response.vectors)}"
            return
        result.dimensions = response.dimensions
        if endpoint.dimensions and response.dimensions != endpoint.dimensions:
            result.detail = f"维度不符：配置 {endpoint.dimensions}，实际 {response.dimensions}"
            return
        result.ok = True
        result.detail = f"Embedding 正常，维度 {response.dimensions}"

    async def _test_reranker(self, endpoint: EndpointConfig, result: EndpointTestResult) -> None:
        candidates = [
            RerankCandidate(id="a", text="光合作用把光能转化为化学能"),
            RerankCandidate(id="b", text="秦始皇统一六国"),
            RerankCandidate(id="c", text="光合作用发生在叶绿体中"),
        ]
        response = await self._invoke(
            endpoint,
            lambda adapter: adapter.rerank("光合作用的场所", candidates, top_n=3),
        )
        self._record(endpoint, response.usage)
        if not response.hits:
            result.detail = "Reranker 未返回排序结果"
            return
        result.ok = True
        result.detail = f"Reranker 正常，返回 {len(response.hits)} 条，Top1={response.hits[0].id}"

    async def _test_ocr(
        self, endpoint: EndpointConfig, result: EndpointTestResult, image: bytes | None
    ) -> None:
        probe = image or make_probe_image()
        if not probe:
            result.detail = "需要测试图片（当前环境未安装 Pillow，无法自动生成）"
            return
        response = await self._invoke(endpoint, lambda adapter: adapter.recognize(probe))
        self._record(endpoint, response.usage)
        if not response.text.strip():
            result.detail = "OCR 连接成功，但未识别出文本（可换一张包含文字的图片重试）"
            result.ok = True
            result.warnings.append("识别结果为空，请确认服务语言与图片内容")
            return
        result.ok = True
        result.detail = f"OCR 正常，识别 {len(response.text)} 个字符"

    async def _test_vlm(
        self, endpoint: EndpointConfig, result: EndpointTestResult, image: bytes | None
    ) -> None:
        probe = image or make_probe_image()
        if not probe:
            result.detail = "需要测试图片（当前环境未安装 Pillow，无法自动生成）"
            return
        response = await self._invoke(
            endpoint,
            lambda adapter: adapter.understand(probe, "请用一句中文描述这张图片。", mime="image/png"),
        )
        self._record(endpoint, response.usage)
        if not response.text.strip():
            result.detail = "视觉模型返回空内容"
            return
        result.ok = True
        result.detail = f"视觉模型正常：{response.text.strip()[:80]}"


def unmapped_error(exc: Exception) -> ModelError:
    """Normalise unexpected exceptions for API responses."""
    if isinstance(exc, ModelError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        return ModelUnavailable("模型请求超时")
    if isinstance(exc, httpx.HTTPError):
        return ModelUnavailable("模型服务连接失败")
    if isinstance(exc, (ModelAuthError, ModelBadResponse)):
        return exc
    return ModelBadResponse(f"{type(exc).__name__}: {exc}")