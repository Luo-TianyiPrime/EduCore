"""OCR JSON parsing.

Accepts the common shapes produced by cloud OCR APIs and PaddleOCR-style
services.  Unknown fields are preserved verbatim in ``meta.raw_keys`` rather
than being guessed at; a structure that cannot be recognised raises
:class:`ParseError`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.core.documents.types import (
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    ParsedSection,
    ParseError,
    clean_text,
)

_BLOCK_TEXT_KEYS = ("text", "content", "transcription", "words", "line")
_BLOCK_LIST_KEYS = ("blocks", "lines", "items", "paragraphs", "words")
_BBOX_KEYS = ("bbox", "box", "bounding_box", "rect")


def parse_ocr_json(path: Path) -> ParsedDocument:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError:
        try:
            data = json.loads(path.read_text(encoding="gb18030"))
        except (UnicodeDecodeError, OSError) as exc:
            raise ParseError("OCR JSON 编码无法识别", detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise ParseError("OCR JSON 无法解析", detail=str(exc)) from exc

    pages_payload = _extract_pages(data)
    if pages_payload is None:
        raise ParseError(
            "无法识别的 OCR JSON 结构",
            detail="支持 pages 数组、页对象数组、PaddleOCR rec_texts 或纯文本对象。",
        )

    pages: list[ParsedPage] = []
    sections: list[ParsedSection] = []
    for index, item in enumerate(pages_payload):
        number = _page_number(item, index + 1)
        text, blocks = _page_text_and_blocks(item)
        page = ParsedPage(
            number=number,
            text=clean_text(text),
            has_text=bool(text.strip()),
            label=_as_str(item.get("label")) if isinstance(item, dict) else None,
            width=_as_float(item.get("width")) if isinstance(item, dict) else None,
            height=_as_float(item.get("height")) if isinstance(item, dict) else None,
            ocr_used=True,
        )
        pages.append(page)
        sections.append(
            ParsedSection(
                title=f"第 {number} 页",
                level=1,
                page_start=number,
                page_end=number,
                blocks=[
                    ParsedBlock(
                        kind="paragraph",
                        text=block["text"],
                        page=number,
                        bbox=block["bbox"],
                        meta=block.get("meta", {}),
                    )
                    for block in blocks
                    if block["text"].strip()
                ],
            )
        )

    title = None
    if isinstance(data, dict):
        title = _as_str(data.get("title") or data.get("name"))
        meta_keys = sorted(data.keys())
    else:
        meta_keys = []
    name = path.stem
    meta = {"raw_keys": meta_keys}
    if isinstance(data, dict):
        meta.update({key: data[key] for key in ("metadata", "meta") if key in data and isinstance(data[key], dict)})

    return ParsedDocument(
        title=title or name,
        format="ocr_json",
        pages=pages,
        sections=sections,
        meta=meta,
    )


def _extract_pages(data: Any) -> list[Any] | None:
    if isinstance(data, list) and data:
        return data
    if not isinstance(data, dict):
        return None

    if isinstance(data.get("pages"), list):
        return data["pages"]

    paddle = data.get("result") or data.get("results")
    if isinstance(paddle, dict):
        ocr_results = paddle.get("ocrResults")
        if isinstance(ocr_results, list):
            return [_paddle_page(item) for item in ocr_results]

    if isinstance(data.get("text"), str):
        return [data]

    if data.get("rec_texts"):
        return [_paddle_page({"prunedResult": data})]

    return None


def _paddle_page(item: Any) -> dict[str, Any]:
    """Flatten PaddleOCR's ``prunedResult`` into the generic page shape."""
    if not isinstance(item, dict):
        return {}
    pruned = item.get("prunedResult") if isinstance(item.get("prunedResult"), dict) else item
    texts = pruned.get("rec_texts")
    if not isinstance(texts, list):
        return pruned
    blocks: list[dict[str, Any]] = []
    scores = pruned.get("rec_scores") or []
    polys = pruned.get("rec_polys") or pruned.get("rec_boxes") or []
    for index, text in enumerate(texts):
        if not isinstance(text, str):
            continue
        bbox = None
        if index < len(polys):
            bbox = _poly_to_bbox(polys[index])
        confidence = None
        if index < len(scores) and isinstance(scores[index], (int, float)):
            confidence = float(scores[index])
        blocks.append({"text": text, "bbox": bbox, "confidence": confidence})
    return {"text": "\n".join(str(text) for text in texts), "blocks": blocks}


def _page_number(item: Any, fallback: int) -> int:
    if not isinstance(item, dict):
        return fallback
    for key in ("page", "page_number", "pageNumber", "page_index", "pageIndex", "index", "number"):
        value = item.get(key)
        if isinstance(value, int):
            # page_index is 0-based; treat 0 as page 1.
            return value + 1 if key.lower() in {"page_index", "pageindex"} else max(1, value)
    return fallback


def _page_text_and_blocks(item: Any) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(item, str):
        return item, [{"text": item, "bbox": None}]
    if not isinstance(item, dict):
        return "", []

    blocks: list[dict[str, Any]] = []
    for key in _BLOCK_LIST_KEYS:
        value = item.get(key)
        if isinstance(value, list):
            for entry in value:
                block = _entry_to_block(entry)
                if block:
                    blocks.append(block)
            if blocks:
                break

    text = _as_str(item.get("text") or item.get("content") or item.get("transcription"))
    if not text:
        text = "\n".join(block["text"] for block in blocks)
    return text or "", blocks


def _entry_to_block(entry: Any) -> dict[str, Any] | None:
    if isinstance(entry, str):
        return {"text": entry, "bbox": None}
    if not isinstance(entry, dict):
        return None
    text = ""
    for key in _BLOCK_TEXT_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            text = value
            break
    if not text:
        return None
    bbox = None
    for key in _BBOX_KEYS:
        if key in entry:
            bbox = _poly_to_bbox(entry[key])
            if bbox:
                break
    meta: dict[str, Any] = {}
    if isinstance(entry.get("confidence"), (int, float)):
        meta["confidence"] = float(entry["confidence"])
    return {"text": text, "bbox": bbox, "meta": meta}


def _poly_to_bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    # Polygon as [[x, y], ...] (PaddleOCR rec_polys) takes priority.
    if all(isinstance(item, (list, tuple)) and len(item) >= 2 for item in value):
        xs = [float(item[0]) for item in value]
        ys = [float(item[1]) for item in value]
        return (min(xs), min(ys), max(xs), max(ys))
    numbers = [item for item in value if isinstance(item, (int, float))]
    if len(numbers) < 4:
        return None
    if len(value) == 4:
        x0, y0, x1, y1 = (float(numbers[0]), float(numbers[1]), float(numbers[2]), float(numbers[3]))
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    xs = numbers[0::2]
    ys = numbers[1::2]
    return (min(xs), min(ys), max(xs), max(ys))


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None