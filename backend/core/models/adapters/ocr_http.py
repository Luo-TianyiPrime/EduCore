"""OCR adapters: generic HTTP service and user-supplied local CLI.

The generic HTTP adapter deliberately supports two wire shapes so that both
cloud OCR APIs and self-hosted PaddleOCR-style services can be used without
writing a new adapter: multipart upload (default) and JSON base64.  Response
text extraction first honours an explicit dotted ``text_path`` and otherwise
falls back to common layouts, including PaddleOCR's ``rec_texts`` arrays.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any

from backend.core.models.adapters.base import HttpAdapterBase, dump_short, get_path
from backend.core.models.types import (
    ModelBadResponse,
    ModelTimeout,
    OCRBlock,
    OCRResponse,
    Usage,
)

logger = logging.getLogger(__name__)


def _flatten_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return "\n".join(item for item in value if item.strip()) or None
    return None


def _find_rec_texts(node: Any, depth: int = 0) -> list[str] | None:
    """Find PaddleOCR-style ``rec_texts`` arrays inside an arbitrary payload."""
    if depth > 6:
        return None
    if isinstance(node, dict):
        for key in ("rec_texts", "texts", "lines_text"):
            found = _flatten_text(node.get(key))
            if found:
                return found.split("\n")
        for value in node.values():
            result = _find_rec_texts(value, depth + 1)
            if result:
                return result
    elif isinstance(node, list):
        for item in node:
            result = _find_rec_texts(item, depth + 1)
            if result:
                return result
    return None


def _extract_text(data: dict[str, Any], configured_path: str | None) -> str:
    if configured_path:
        value = get_path(data, configured_path)
        text = _flatten_text(value)
        if text:
            return text
        raise ModelBadResponse(f"OCR 响应路径 {configured_path} 无文本", detail=dump_short(data))

    for path in ("text", "result.text", "data.text", "ocr_text", "result"):
        value = get_path(data, path)
        text = _flatten_text(value)
        if text:
            return text

    texts = _find_rec_texts(data)
    if texts:
        return "\n".join(texts)
    raise ModelBadResponse("OCR 响应中未找到文本", detail=dump_short(data))


class OCRHttpAdapter(HttpAdapterBase):
    async def recognize(self, image: bytes, *, mime: str = "image/png") -> OCRResponse:
        mode = str(self.endpoint.extra.get("mode") or "multipart")
        field = str(self.endpoint.extra.get("field") or "image")
        url = self.resolve_url(str(self.endpoint.extra.get("path") or "")) if self.endpoint.extra.get("path") else self.base_url

        if mode == "json_base64":
            payload: dict[str, Any] = {field: base64.b64encode(image).decode("ascii")}
            payload.update(self.endpoint.extra.get("json_extra") or {})
            data = await self.post_json(url, payload)
        else:
            form: dict[str, str] = {}
            form.update({str(k): str(v) for k, v in (self.endpoint.extra.get("form") or {}).items()})
            data = await self.post_multipart(
                url,
                files={field: ("page.png", image, mime)},
                data=form,
            )

        text = _extract_text(data, self.endpoint.extra.get("text_path"))
        blocks = self._extract_blocks(data)
        return OCRResponse(
            text=text,
            blocks=blocks,
            usage=Usage(calls=1),
            raw=data if self.endpoint.extra.get("keep_raw") else None,
            model=self.endpoint.model or "ocr",
        )

    def _extract_blocks(self, data: dict[str, Any]) -> list[OCRBlock]:
        path = self.endpoint.extra.get("blocks_path")
        if not path:
            return []
        value = get_path(data, str(path))
        if not isinstance(value, list):
            return []
        blocks: list[OCRBlock] = []
        for item in value:
            if isinstance(item, str):
                blocks.append(OCRBlock(text=item))
                continue
            if not isinstance(item, dict):
                continue
            text = item.get("text") or item.get("content")
            if not isinstance(text, str) or not text.strip():
                continue
            bbox = item.get("bbox") or item.get("box")
            bbox_tuple = None
            if isinstance(bbox, list) and len(bbox) == 4 and all(
                isinstance(value, (int, float)) for value in bbox
            ):
                bbox_tuple = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
            confidence = item.get("confidence", item.get("score"))
            blocks.append(
                OCRBlock(
                    text=text,
                    bbox=bbox_tuple,
                    confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
                )
            )
        return blocks


class CLIOCRAdapter:
    """Runs a user-provided OCR executable and reads text from stdout."""

    def __init__(self, endpoint, api_key: str | None = None) -> None:
        self.endpoint = endpoint

    async def recognize(self, image: bytes, *, mime: str = "image/png") -> OCRResponse:
        command = self.endpoint.extra.get("command") or ""
        suffix = ".png" if "png" in mime else ".jpg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(image)
            image_path = Path(handle.name)

        try:
            args = self._build_args(str(command), image_path)
            try:
                process = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise ModelBadResponse(
                    f"本地 OCR 命令不存在: {args[0]}", detail=str(exc)
                ) from exc

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.endpoint.timeout_s
                )
            except TimeoutError as exc:
                process.kill()
                raise ModelTimeout(f"本地 OCR 命令超时（{self.endpoint.timeout_s}s）") from exc

            if process.returncode != 0:
                raise ModelBadResponse(
                    f"本地 OCR 命令退出码 {process.returncode}",
                    detail=stderr.decode("utf-8", "replace")[:500],
                )
            return OCRResponse(
                text=self._parse_stdout(stdout.decode("utf-8", "replace")),
                usage=Usage(calls=1),
                model=self.endpoint.model or "cli-ocr",
            )
        finally:
            image_path.unlink(missing_ok=True)

    def _build_args(self, command: str, image_path: Path) -> list[str]:
        posix = os.name != "nt"
        parts = shlex.split(command, posix=posix)
        if not parts:
            raise ModelBadResponse("本地 OCR 命令为空")
        page = str(self.endpoint.extra.get("page") or "")
        language = str(self.endpoint.extra.get("language") or "")
        return [
            part.replace("{image}", str(image_path))
            .replace("{page}", page)
            .replace("{lang}", language)
            for part in parts
        ]

    def _parse_stdout(self, stdout: str) -> str:
        text = stdout.strip()
        if not text:
            return ""
        try:
            payload = json.loads(text)
        except ValueError:
            return text
        found = _extract_text(payload, self.endpoint.extra.get("text_path")) if isinstance(payload, dict) else None
        if found:
            return found
        if isinstance(payload, list) and all(isinstance(item, str) for item in payload):
            return "\n".join(payload)
        return text