"""Reranker adapter for the common ``/rerank`` HTTP shape.

Supported response layouts (auto-detected):
  {"results": [{"index": 0, "relevance_score": 0.9}]}   # Jina / Cohere / SiliconFlow
  {"data":    [{"index": 0, "score": 0.9}]}
  {"output":  {"results": [...]}}
"""

from __future__ import annotations

from typing import Any

from backend.core.models.adapters.base import HttpAdapterBase, dump_short, get_path
from backend.core.models.types import (
    ModelBadResponse,
    RerankCandidate,
    RerankHit,
    RerankResponse,
    Usage,
)


class RerankHTTP(HttpAdapterBase):
    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        top_n: int | None = None,
    ) -> RerankResponse:
        if not candidates:
            return RerankResponse(hits=[])

        payload: dict[str, Any] = {
            "model": self.endpoint.model,
            "query": query,
            "documents": [candidate.text for candidate in candidates],
        }
        if top_n is not None:
            payload["top_n"] = max(1, min(top_n, len(candidates)))
        payload.update(self.endpoint.extra.get("request_extra") or {})

        path = str(self.endpoint.extra.get("path") or "rerank")
        data = await self.post_json(self.resolve_url(path), payload)

        items = self._extract_results(data)
        if items is None:
            raise ModelBadResponse(
                f"{self.endpoint.name} 返回结构无法识别（缺少 results）", detail=dump_short(data)
            )

        hits: list[RerankHit] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_index = item.get("index")
            if raw_index is None:
                continue
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if not 0 <= index < len(candidates):
                continue
            score = item.get("relevance_score", item.get("score"))
            if score is None:
                continue
            hits.append(
                RerankHit(id=candidates[index].id, score=float(score), index=index)
            )

        hits.sort(key=lambda hit: hit.score, reverse=True)
        usage_block = data.get("usage") or {}
        usage = Usage(
            calls=1,
            input_tokens=int(usage_block.get("prompt_tokens") or usage_block.get("total_tokens") or 0),
        )
        if not hits:
            raise ModelBadResponse(
                f"{self.endpoint.name} 未返回有效排序结果", detail=dump_short(data)
            )
        return RerankResponse(
            hits=hits,
            usage=usage,
            model=str(data.get("model") or self.endpoint.model),
        )

    def _extract_results(self, data: dict[str, Any]) -> list[Any] | None:
        configured = self.endpoint.extra.get("results_path")
        if configured:
            value = get_path(data, str(configured))
            return value if isinstance(value, list) else None
        for path in ("results", "data", "output.results", "result.results"):
            value = get_path(data, path)
            if isinstance(value, list):
                return value
        return None