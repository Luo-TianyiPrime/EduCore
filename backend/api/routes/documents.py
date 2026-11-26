"""Document listing, structure reading, rendering and OCR endpoints."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response

from backend.api.deps import IndexDep, RuntimeDep
from backend.api.schemas import DocumentIn, OcrPagesRequest
from backend.core.documents.pdf import render_page_png
from backend.core.documents.types import ParseError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


@router.get("")
def list_documents(
    index: IndexDep,
    source_id: str | None = None,
    status: str | None = None,
    query: str | None = None,
    include_missing: bool = False,
    limit: int = Query(default=300, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    items = index.documents.list(
        source_id=source_id,
        status=status,
        query=query.strip() if query else None,
        include_missing=include_missing,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "counts": index.stats()}


@router.post("", status_code=201)
async def add_document(payload: DocumentIn, index: IndexDep) -> dict:
    try:
        document = index.add_file(payload.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    jobs = index.start_scan(document_id=document["id"])
    return {"document": document, "jobs": jobs}


@router.get("/{document_id}")
def get_document(document_id: str, index: IndexDep) -> dict:
    document = index.documents.get(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    version = None
    sections: list[dict] = []
    if document["current_version_id"]:
        version = index.documents.get_version(document["current_version_id"])
        if version:
            sections = index.documents.sections_with_counts(version["id"])

    return {
        "document": document,
        "version": version,
        "sections": sections,
        "versions": index.documents.versions(document_id)[:10],
    }


@router.get("/{document_id}/sections/{section_id}")
def get_section(document_id: str, section_id: str, index: IndexDep) -> dict:
    document = index.documents.get(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if not document["current_version_id"]:
        raise HTTPException(status_code=409, detail="文档尚未完成解析")
    blocks = index.documents.blocks(document["current_version_id"], section_id)
    return {"section_id": section_id, "blocks": blocks}


@router.get("/{document_id}/pages/{page_number}")
def get_page_image(
    document_id: str,
    page_number: int,
    index: IndexDep,
    runtime: RuntimeDep,
    scale: float = Query(default=2.0, ge=0.5, le=4.0),
) -> Response:
    document = index.documents.get(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    path = Path(document["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="原始文件已不存在")

    if document["format"] == "image":
        mime = _IMAGE_MIME.get(path.suffix.lower(), "image/png")
        return Response(path.read_bytes(), media_type=mime)

    if document["format"] != "pdf":
        raise HTTPException(status_code=404, detail="该格式没有页面图像")

    cache_dir = runtime.paths.cache_dir / "pages" / document_id
    cache_key = f"{(document['content_hash'] or 'nohash')[:16]}-{page_number}-{scale:.1f}.png"
    cached = cache_dir / cache_key
    if cached.is_file():
        return Response(cached.read_bytes(), media_type="image/png")

    try:
        png = render_page_png(path, page_number, scale=scale)
    except ParseError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(".tmp")
        tmp.write_bytes(png)
        tmp.replace(cached)
    except OSError as exc:  # pragma: no cover - cache is best effort
        logger.warning("页面缓存写入失败: %s", exc)
    return Response(png, media_type="image/png")


@router.post("/{document_id}/reindex", status_code=202)
async def reindex_document(document_id: str, index: IndexDep) -> dict:
    try:
        jobs = index.start_scan(document_id=document_id, force=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"jobs": jobs}


@router.post("/{document_id}/ocr")
async def ocr_pages(document_id: str, payload: OcrPagesRequest, index: IndexDep) -> dict:
    if not payload.pages:
        raise HTTPException(status_code=400, detail="请指定要识别的页码")
    try:
        return await index.reocr_pages(document_id, payload.pages)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: str, index: IndexDep) -> Response:
    try:
        index.remove_document(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)