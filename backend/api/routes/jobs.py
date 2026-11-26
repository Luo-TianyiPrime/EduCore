"""Indexing job endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.api.deps import IndexDep, RuntimeDep
from backend.api.schemas import ScanRequest, VectorRebuildRequest

router = APIRouter(prefix="/index", tags=["index"])


@router.get("/jobs")
def list_jobs(index: IndexDep, limit: int = 50) -> dict:
    return {"items": index.list_jobs(limit)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, index: IndexDep) -> dict:
    job = index.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"job": job}


@router.post("/jobs", status_code=202)
async def create_job(payload: ScanRequest, index: IndexDep) -> dict:
    try:
        jobs = index.start_scan(
            source_id=payload.source_id,
            document_id=payload.document_id,
            force=payload.force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"jobs": jobs}


@router.post("/vectors", status_code=202)
async def rebuild_vectors(payload: VectorRebuildRequest, index: IndexDep) -> dict:
    try:
        jobs = index.start_vector_rebuild(
            source_id=payload.source_id,
            document_id=payload.document_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"jobs": jobs}


@router.get("/profiles")
def list_profiles(runtime: RuntimeDep) -> dict:
    embeddings = runtime.embeddings
    vectors = runtime.vectors
    if embeddings is None or vectors is None:
        return {"items": []}
    items = []
    for profile in embeddings.profiles.list():
        endpoint = runtime.config.endpoint(profile["endpoint_id"])
        compatible = bool(
            endpoint
            and endpoint.enabled
            and endpoint.is_configured
            and endpoint.model == profile["model"]
            and (endpoint.dimensions is None or endpoint.dimensions == profile["dimensions"])
        )
        items.append(
            {
                **profile,
                "table_vectors": vectors.count(profile["table_name"]),
                "endpoint_name": endpoint.name if endpoint else None,
                "compatible": compatible,
            }
        )
    return {"items": items}