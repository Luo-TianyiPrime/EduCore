"""Shared HTTP behaviour and error mapping for model adapters."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from backend.core.config import EndpointConfig
from backend.core.models.types import (
    ModelAuthError,
    ModelBadResponse,
    ModelRateLimited,
    ModelTimeout,
    ModelUnavailable,
)

logger = logging.getLogger(__name__)


class HttpAdapterBase:
    def __init__(
        self,
        client: httpx.AsyncClient,
        endpoint: EndpointConfig,
        api_key: str | None,
    ) -> None:
        self.client = client
        self.endpoint = endpoint
        self.api_key = api_key

    # -- URL / headers -----------------------------------------------------
    @property
    def base_url(self) -> str:
        return self.endpoint.base_url.rstrip("/")

    def resolve_url(self, path: str = "") -> str:
        """Join ``path`` onto the configured base URL.

        A base URL that already ends with the target path (``.../chat/completions``)
        is used as-is, which keeps single-endpoint configurations simple.
        """
        if not path:
            return self.base_url
        if self.base_url.endswith(path):
            return self.base_url
        return f"{self.base_url}/{path.lstrip('/')}"

    def headers(self, *, json_body: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if json_body:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        extra_headers = self.endpoint.extra.get("headers")
        if isinstance(extra_headers, dict):
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        return headers

    # -- transport ---------------------------------------------------------
    def _map_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        snippet = response.text[:500]
        if response.status_code in (401, 403):
            raise ModelAuthError(
                f"{self.endpoint.name} 鉴权失败 (HTTP {response.status_code})", detail=snippet
            )
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            try:
                delay = float(retry_after) if retry_after else None
            except ValueError:
                delay = None
            raise ModelRateLimited(f"{self.endpoint.name} 触发限流 (HTTP 429)", retry_after=delay)
        if response.status_code >= 500:
            raise ModelUnavailable(
                f"{self.endpoint.name} 服务不可用 (HTTP {response.status_code})", detail=snippet
            )
        raise ModelBadResponse(
            f"{self.endpoint.name} 请求被拒绝 (HTTP {response.status_code})", detail=snippet
        )

    async def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self.client.post(
                url,
                json=payload,
                headers=self.headers(),
                timeout=timeout or self.endpoint.timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ModelTimeout(f"{self.endpoint.name} 请求超时", detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"{self.endpoint.name} 无法连接", detail=str(exc)) from exc

        self._map_status(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ModelBadResponse(
                f"{self.endpoint.name} 返回了非 JSON 响应", detail=response.text[:500]
            ) from exc
        if not isinstance(data, dict):
            raise ModelBadResponse(f"{self.endpoint.name} 返回结构异常")
        return data

    async def post_multipart(
        self,
        url: str,
        files: dict[str, tuple[str, bytes, str]],
        data: dict[str, str] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self.client.post(
                url,
                files=files,
                data=data or {},
                headers=self.headers(json_body=False),
                timeout=timeout or self.endpoint.timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ModelTimeout(f"{self.endpoint.name} 请求超时", detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"{self.endpoint.name} 无法连接", detail=str(exc)) from exc

        self._map_status(response)
        try:
            data_json = response.json()
        except ValueError as exc:
            raise ModelBadResponse(
                f"{self.endpoint.name} 返回了非 JSON 响应", detail=response.text[:500]
            ) from exc
        if not isinstance(data_json, dict):
            raise ModelBadResponse(f"{self.endpoint.name} 返回结构异常")
        return data_json


def get_path(payload: Any, path: str) -> Any:
    """Read a dotted path such as ``data.result.text`` from nested JSON."""
    current = payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def dump_short(value: Any, limit: int = 400) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    return text[:limit]
