"""Page-level OCR with caching and graceful degradation."""

from __future__ import annotations

import asyncio
from pathlib import Path

from backend.core.documents.hashing import sha256_bytes
from backend.core.documents.pdf import render_page_png
from backend.core.models.gateway import ModelGateway
from backend.core.models.types import (
    ModelError,
    ModelNotConfigured,
    OCRBlock,
    OCRResponse,
)
from backend.storage.ocr_cache import OcrCacheRepo


class OCRService:
    def __init__(self, gateway: ModelGateway, cache: OcrCacheRepo) -> None:
        self.gateway = gateway
        self.cache = cache

    def configured(self) -> bool:
        try:
            self.gateway.resolve("ocr")
            return True
        except ModelNotConfigured:
            return False

    async def recognize_image(self, image: bytes, *, mime: str = "image/png") -> OCRResponse:
        endpoint = self.gateway.resolve("ocr")
        digest = sha256_bytes(image)
        cached = self.cache.get(digest, endpoint.id)
        if cached is not None:
            return OCRResponse(
                text=cached["text"],
                blocks=[
                    OCRBlock(
                        text=block["text"],
                        bbox=tuple(block["bbox"]) if block.get("bbox") else None,
                        confidence=block.get("confidence"),
                    )
                    for block in cached["blocks"]
                ],
                model=endpoint.model,
            )

        response = await self.gateway.ocr(image, endpoint_id=endpoint.id, mime=mime)
        self.cache.put(
            image_hash=digest,
            endpoint_id=endpoint.id,
            text=response.text,
            blocks=[
                {
                    "text": block.text,
                    "bbox": list(block.bbox) if block.bbox else None,
                    "confidence": block.confidence,
                }
                for block in response.blocks
            ],
        )
        return response

    async def recognize_pdf_page(self, path: Path, page_number: int) -> OCRResponse:
        image = await asyncio.to_thread(
            render_page_png, path, page_number, scale=300 / 72
        )
        return await self.recognize_image(image)

    async def recognize_image_file(self, path: Path) -> OCRResponse:
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
        }.get(path.suffix.lower(), "image/png")
        image = await asyncio.to_thread(path.read_bytes)
        return await self.recognize_image(image, mime=mime)


def ocr_error_message(exc: Exception) -> str:
    if isinstance(exc, ModelError):
        return f"{exc.message}"
    return f"{type(exc).__name__}: {exc}"