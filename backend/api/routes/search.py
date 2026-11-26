"""Hybrid knowledge search endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.api.deps import RuntimeDep
from backend.api.schemas import SearchRequest

router = APIRouter(prefix="/search", tags=["search"])


@router.post("")
async def search(payload: SearchRequest, runtime: RuntimeDep) -> dict:
    service = runtime.retrieval
    if service is None:  # pragma: no cover - lifespan guarantees this
        raise HTTPException(status_code=500, detail="检索服务未初始化")
    try:
        return await service.search(
            payload.query,
            document_ids=payload.document_ids,
            source_ids=payload.source_ids,
            top_k=payload.top_k,
            use_reranker=payload.use_reranker,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc