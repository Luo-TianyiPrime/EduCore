"""Artifact endpoints: list, preview, edit, regenerate and export."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from backend.api.deps import RuntimeDep
from backend.api.schemas import (
    ArtifactExportRequest,
    ArtifactRegenerateRequest,
    ArtifactUpdateRequest,
)

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def _service(runtime):
    if runtime.artifacts is None:  # pragma: no cover
        raise HTTPException(status_code=500, detail="成果服务未初始化")
    return runtime.artifacts


@router.get("")
def list_artifacts(
    runtime: RuntimeDep,
    task_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    return {"items": _service(runtime).list(task_id=task_id, limit=limit)}


@router.get("/{artifact_id}")
def get_artifact(artifact_id: str, runtime: RuntimeDep) -> dict:
    try:
        return _service(runtime).detail_with_validation(artifact_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{artifact_id}")
def update_artifact(artifact_id: str, payload: ArtifactUpdateRequest, runtime: RuntimeDep) -> dict:
    try:
        return _service(runtime).update_markdown(artifact_id, payload.markdown)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{artifact_id}/regenerate")
async def regenerate_artifact(
    artifact_id: str, payload: ArtifactRegenerateRequest, runtime: RuntimeDep
) -> dict:
    try:
        return await _service(runtime).regenerate_section(
            artifact_id,
            heading=payload.heading,
            instruction=payload.instruction,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{artifact_id}/export")
async def export_artifact(
    artifact_id: str, payload: ArtifactExportRequest, runtime: RuntimeDep
) -> dict:
    try:
        return await _service(runtime).export(
            artifact_id,
            format=payload.format,
            output_dir=payload.output_dir,
            include_citations=payload.include_citations,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{artifact_id}/download/{format}")
def download_artifact(artifact_id: str, format: str, runtime: RuntimeDep) -> FileResponse:
    path: Path | None = _service(runtime).latest_export(artifact_id, format)
    if path is None:
        raise HTTPException(status_code=404, detail="尚未导出该格式")
    return FileResponse(path, filename=path.name)


@router.get("/{artifact_id}/validation")
def validate_artifact(artifact_id: str, runtime: RuntimeDep) -> dict:
    try:
        return _service(runtime).detail_with_validation(artifact_id)["validation"]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc