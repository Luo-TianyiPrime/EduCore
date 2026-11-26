"""Source folder endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from backend.api.deps import IndexDep, RuntimeDep
from backend.api.schemas import SourceIn

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("")
def list_sources(index: IndexDep, runtime: RuntimeDep) -> dict:
    return {
        "items": index.list_sources(),
        "stats": index.stats(),
        "data_dir": str(runtime.paths.root),
    }


@router.post("", status_code=201)
async def add_source(payload: SourceIn, index: IndexDep) -> dict:
    try:
        source = index.add_source(payload.path, payload.recursive)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    jobs: list[dict] = []
    if payload.scan:
        jobs = index.start_scan(source_id=source["id"])
    return {"source": source, "jobs": jobs}


@router.post("/{source_id}/scan")
async def scan_source(source_id: str, index: IndexDep) -> dict:
    try:
        jobs = index.start_scan(source_id=source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"jobs": jobs}


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: str, index: IndexDep) -> Response:
    try:
        index.remove_source(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)