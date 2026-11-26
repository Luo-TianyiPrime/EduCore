"""Hybrid retrieval pipeline: multi-space vectors + BM25 + RRF + rerank.

Each embedding profile is queried with an encoder that is verified to be
compatible (same endpoint, model, dimensions and query instruction).  Results
are fused by rank only; incompatible spaces are skipped with a visible
degradation note instead of being mixed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

from backend.core.config import AppConfig
from backend.core.indexing.fts import build_match_query
from backend.core.models.gateway import ModelGateway
from backend.core.models.types import ModelError, ModelNotConfigured, RerankCandidate
from backend.core.retrieval.rrf import reciprocal_rank_fusion
from backend.core.retrieval.vector_store import VectorStore
from backend.storage.db import Database
from backend.storage.documents import DocumentRepo
from backend.storage.embedding_profiles import EmbeddingProfilesRepo
from backend.storage.index_store import IndexStore

logger = logging.getLogger(__name__)


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


class RetrievalService:
    def __init__(
        self,
        *,
        db: Database,
        gateway: ModelGateway,
        vectors: VectorStore,
        config_provider: Callable[[], AppConfig],
    ) -> None:
        self.db = db
        self.gateway = gateway
        self.vectors = vectors
        self.config_provider = config_provider
        self.documents = DocumentRepo(db)
        self.index = IndexStore(db)
        self.profiles = EmbeddingProfilesRepo(db)
        # Temp tables used for scope filtering are per connection; serialise
        # scoped queries so concurrent searches cannot clobber each other.
        self._scope_lock = asyncio.Lock()

    async def search(
        self,
        query: str,
        *,
        document_ids: Sequence[str] | None = None,
        source_ids: Sequence[str] | None = None,
        top_k: int | None = None,
        use_reranker: bool = True,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        text = (query or "").strip()
        if not text:
            raise ValueError("查询内容不能为空")

        config = self.config_provider()
        settings = config.retrieval
        final_top = max(1, min(top_k or settings.final_top, 50))
        degraded: list[str] = []
        timings: dict[str, int] = {}

        # -- scope ---------------------------------------------------------
        scoped = await asyncio.to_thread(self._resolve_scope, document_ids, source_ids)
        scope_documents = len({row["document_id"] for row in scoped})
        if not scoped:
            return self._empty_response(
                text,
                "所选范围内没有已索引的文档",
                timings_ms=_ms(started),
            )
        version_ids = [row["version_id"] for row in scoped]
        timings["scope"] = _ms(started)

        # -- vector recall, one query per compatible space ------------------
        vector_started = time.perf_counter()
        by_profile: dict[str, list[str]] = defaultdict(list)
        for row in scoped:
            if row["vector_status"] == "ready" and row["embedding_profile_id"]:
                by_profile[row["embedding_profile_id"]].append(row["version_id"])
        if not by_profile:
            degraded.append("范围内没有可用的向量索引，本次仅使用关键词检索")
        vector_lists: list[list[str]] = []
        profile_debug: list[dict[str, Any]] = []
        vector_rank: dict[str, int] = {}
        for profile_id, versions in by_profile.items():
            profile = self.profiles.get(profile_id)
            if profile is None:
                degraded.append(f"向量空间 {profile_id} 的档案缺失，已跳过")
                continue
            ranked, note, candidates = await self._recall_profile(
                text, profile, versions, config, settings.vector_candidates
            )
            profile_debug.append(
                {
                    "profile_id": profile_id,
                    "endpoint_id": profile["endpoint_id"],
                    "model": profile["model"],
                    "dimensions": profile["dimensions"],
                    "versions": len(versions),
                    "candidates": candidates,
                    "note": note,
                }
            )
            if note:
                degraded.append(note)
            if ranked:
                vector_lists.append(ranked)
                for rank, chunk_id in enumerate(ranked, start=1):
                    vector_rank.setdefault(chunk_id, rank)
        timings["vector"] = _ms(vector_started)

        # -- BM25 recall ----------------------------------------------------
        bm25_started = time.perf_counter()
        match_query = build_match_query(text)
        bm25_rows: list[dict[str, Any]] = []
        trigram_used = False
        async with self._scope_lock:
            if match_query:
                bm25_rows = await asyncio.to_thread(
                    self.index.search_fts_scoped,
                    match_query,
                    version_ids,
                    settings.bm25_candidates,
                )
            if not bm25_rows:
                trigram_rows = await asyncio.to_thread(
                    self.index.search_trigram_scoped,
                    text,
                    version_ids,
                    settings.bm25_candidates,
                )
                if trigram_rows:
                    bm25_rows = trigram_rows
                    trigram_used = True
        bm25_rank = {row["chunk_id"]: rank for rank, row in enumerate(bm25_rows, start=1)}
        bm25_list = [row["chunk_id"] for row in bm25_rows]
        timings["bm25"] = _ms(bm25_started)

        # -- fusion ---------------------------------------------------------
        fused_started = time.perf_counter()
        fused = reciprocal_rank_fusion([*vector_lists, bm25_list], k=settings.rrf_k)
        fused_scores = dict(fused)
        fused_ids = [chunk_id for chunk_id, _score in fused[: settings.fusion_top]]
        chunk_map: dict[str, dict[str, Any]] = {}
        if fused_ids:
            async with self._scope_lock:
                rows = await asyncio.to_thread(self.documents.chunks_by_ids, fused_ids)
            chunk_map = {row["id"]: row for row in rows}
            missing = [chunk_id for chunk_id in fused_ids if chunk_id not in chunk_map]
            if missing:
                degraded.append(f"{len(missing)} 个候选块已失效（文档可能已更新）")
        candidates = [chunk_map[chunk_id] for chunk_id in fused_ids if chunk_id in chunk_map]
        timings["fusion"] = _ms(fused_started)

        # -- rerank ---------------------------------------------------------
        rerank_started = time.perf_counter()
        reranked = False
        rerank_scores: dict[str, float] = {}
        rerank_note: str | None = None
        if use_reranker and candidates:
            try:
                reranker = self.gateway.resolve("reranker")
            except ModelNotConfigured:
                reranker = None
                rerank_note = "未配置 Reranker，已使用 RRF 融合排序"
            if reranker is not None:
                try:
                    response = await self.gateway.rerank(
                        text,
                        [
                            RerankCandidate(
                                id=chunk["id"],
                                text=(f"{chunk['heading']}\n{chunk['text']}").strip()[:4000],
                            )
                            for chunk in candidates
                        ],
                        endpoint_id=reranker.id,
                        top_n=len(candidates),
                    )
                    order = {hit.id: index for index, hit in enumerate(response.hits)}
                    rerank_scores = {hit.id: hit.score for hit in response.hits}
                    candidates.sort(key=lambda chunk: order.get(chunk["id"], len(order)))
                    reranked = True
                except ModelError as exc:
                    rerank_note = f"Reranker 调用失败（{exc.message}），已使用 RRF 融合排序"
        if rerank_note:
            degraded.append(rerank_note)
        timings["rerank"] = _ms(rerank_started)

        # -- expand ---------------------------------------------------------
        expand_started = time.perf_counter()
        selected = candidates[:final_top]
        hits = [
            await asyncio.to_thread(
                self._build_hit,
                chunk,
                fused_scores.get(chunk["id"], 0.0),
                rerank_scores.get(chunk["id"]),
                vector_rank.get(chunk["id"]),
                bm25_rank.get(chunk["id"]),
            )
            for chunk in selected
        ]
        timings["expand"] = _ms(expand_started)
        timings["total"] = _ms(started)

        return {
            "query": text,
            "hits": hits,
            "debug": {
                "scope_documents": scope_documents,
                "scope_versions": len(version_ids),
                "profiles": profile_debug,
                "vector_candidates": len(vector_rank),
                "bm25_candidates": len(bm25_rows),
                "trigram_used": trigram_used,
                "fused_candidates": len(fused),
                "reranked": reranked,
                "degraded": degraded,
                "timings_ms": timings,
            },
        }

    # -- internals ---------------------------------------------------------
    def _resolve_scope(
        self,
        document_ids: Sequence[str] | None,
        source_ids: Sequence[str] | None,
    ) -> list[dict[str, Any]]:
        rows = self.documents.current_versions()
        if document_ids:
            wanted = set(document_ids)
            rows = [row for row in rows if row["document_id"] in wanted]
        if source_ids:
            wanted_sources = set(source_ids)
            rows = [row for row in rows if row["source_id"] in wanted_sources]
        return rows

    async def _recall_profile(
        self,
        query: str,
        profile: dict[str, Any],
        version_ids: list[str],
        config: AppConfig,
        limit: int,
    ) -> tuple[list[str], str | None, int]:
        endpoint = config.endpoint(profile["endpoint_id"])
        if endpoint is None:
            return [], f"向量空间 {profile['model']} 的原端点已删除，该索引暂不可用", 0
        if not endpoint.enabled or not endpoint.is_configured:
            return [], f"向量空间 {profile['model']} 的端点已停用，该索引暂不可用", 0
        if endpoint.model != profile["model"]:
            return (
                [],
                f"端点 {endpoint.name} 的模型已从 {profile['model']} 改为 {endpoint.model}，旧索引不参与向量检索",
                0,
            )
        if endpoint.dimensions and endpoint.dimensions != profile["dimensions"]:
            return (
                [],
                f"向量空间 {profile['model']} 维度不符（{profile['dimensions']} vs {endpoint.dimensions}）",
                0,
            )
        if (endpoint.query_instruction or "") != (profile["query_instruction"] or ""):
            return (
                [],
                f"向量空间 {profile['model']} 的查询指令已变更，需重建索引后再参与向量检索",
                0,
            )

        try:
            response = await self.gateway.embed([query], endpoint_id=endpoint.id, role="query")
        except ModelError as exc:
            return [], f"向量空间 {profile['model']} 查询编码失败：{exc.message}", 0
        if not response.vectors or response.dimensions != profile["dimensions"]:
            return [], f"向量空间 {profile['model']} 返回维度异常", 0

        try:
            hits = await asyncio.to_thread(
                self.vectors.search,
                profile["table_name"],
                response.vectors[0],
                limit=limit,
                version_ids=version_ids,
                distance=profile["distance"],
            )
        except Exception as exc:  # pragma: no cover - storage failure
            logger.warning("向量检索失败 %s: %s", profile["table_name"], exc)
            return [], f"向量空间 {profile['model']} 检索失败：{type(exc).__name__}", 0

        ranked = [hit["chunk_id"] for hit in hits if hit.get("chunk_id")]
        return ranked, None, len(ranked)

    def _build_hit(
        self,
        chunk: dict[str, Any],
        rrf_score: float,
        rerank_score: float | None,
        vector_rank: int | None,
        bm25_rank: int | None,
        *,
        max_context_chars: int = 2000,
    ) -> dict[str, Any]:
        section_path: list[str] = []
        section = self.documents.section(chunk["section_id"])
        seen: set[str] = set()
        while section and len(section_path) < 10:
            section_id = section["id"]
            if section_id in seen:
                break
            seen.add(section_id)
            section_path.append(section["title"] or "未命名章节")
            parent_id = section.get("parent_id")
            section = self.documents.section(parent_id) if parent_id else None
        section_path.reverse()

        context_parts: list[str] = []
        if chunk.get("section_id"):
            blocks = self.documents.blocks(chunk["version_id"], chunk["section_id"])
            section_text = "\n\n".join(block["text"] for block in blocks if block["text"].strip())
            if section_text:
                context_parts.append(section_text)
        for neighbour in self.documents.neighbor_chunks(
            chunk["version_id"], chunk["order_index"], 1
        ):
            if neighbour["id"] == chunk["id"]:
                continue
            context_parts.append(f"[相邻]{neighbour['heading']}\n{neighbour['text']}")
        context = "\n\n".join(part for part in context_parts if part.strip())
        if len(context) > max_context_chars:
            context = context[:max_context_chars].rstrip() + "…"

        score = rerank_score if rerank_score is not None else rrf_score
        return {
            "chunk_id": chunk["id"],
            "document_id": chunk["document_id"],
            "document_title": chunk.get("document_title"),
            "document_path": chunk.get("document_path"),
            "document_format": chunk.get("document_format"),
            "heading": chunk["heading"],
            "text": chunk["text"],
            "page_start": chunk["page_start"],
            "page_end": chunk["page_end"],
            "section_id": chunk["section_id"],
            "section_path": section_path,
            "context": context,
            "score": float(score),
            "rrf_score": float(rrf_score),
            "rerank_score": float(rerank_score) if rerank_score is not None else None,
            "vector_rank": vector_rank,
            "bm25_rank": bm25_rank,
        }

    def _empty_response(self, query: str, note: str, *, timings_ms: int) -> dict[str, Any]:
        return {
            "query": query,
            "hits": [],
            "debug": {
                "scope_documents": 0,
                "scope_versions": 0,
                "profiles": [],
                "vector_candidates": 0,
                "bm25_candidates": 0,
                "trigram_used": False,
                "fused_candidates": 0,
                "reranked": False,
                "degraded": [note],
                "timings_ms": {"total": timings_ms},
            },
        }