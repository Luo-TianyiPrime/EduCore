"""Format dispatch and OCR result application."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from backend.core.documents.markdown import parse_markdown
from backend.core.documents.ocr_json import parse_ocr_json
from backend.core.documents.pdf import parse_pdf
from backend.core.documents.types import (
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    ParsedSection,
    ParseError,
    clean_text,
)
from backend.core.models.types import OCRResponse

FORMAT_BY_SUFFIX = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".markdown": "markdown",
    ".json": "ocr_json",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".bmp": "image",
    ".tif": "image",
    ".tiff": "image",
}

SUPPORTED_SUFFIXES = frozenset(FORMAT_BY_SUFFIX)


def detect_format(path: Path) -> str | None:
    return FORMAT_BY_SUFFIX.get(path.suffix.lower())


def parse_file(path: Path) -> ParsedDocument:
    fmt = detect_format(path)
    if fmt is None:
        raise ParseError(f"不支持的文件格式: {path.suffix or '无扩展名'}")
    if fmt == "pdf":
        return parse_pdf(path)
    if fmt == "markdown":
        return parse_markdown(path)
    if fmt == "ocr_json":
        return parse_ocr_json(path)
    if fmt == "image":
        return _parse_image(path)
    raise ParseError(f"未知格式: {fmt}")


def _parse_image(path: Path) -> ParsedDocument:
    width = height = None
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
    except Exception:
        pass
    page = ParsedPage(
        number=1,
        text="",
        has_text=False,
        width=float(width) if width else None,
        height=float(height) if height else None,
    )
    return ParsedDocument(
        title=path.stem,
        format="image",
        pages=[page],
        sections=[
            ParsedSection(title="第 1 页", level=1, page_start=1, page_end=1),
        ],
        meta={"image_size": [width, height] if width and height else None},
    )


def apply_ocr_results(
    document: ParsedDocument,
    results: dict[int, OCRResponse],
) -> None:
    """Replace page text and blocks with OCR output for the given pages."""
    for page_number, response in results.items():
        page = next((item for item in document.pages if item.number == page_number), None)
        if page is None:
            continue
        text = clean_text(response.text)
        page.text = text
        page.has_text = bool(text.strip())
        page.ocr_used = True

        blocks = _ocr_blocks(response, page_number)
        _replace_page_blocks(document, page_number, blocks)
        document.meta.setdefault("ocr_pages", []).append(page_number)


def _ocr_blocks(response: OCRResponse, page_number: int) -> list[ParsedBlock]:
    lines: list[ParsedBlock] = []
    for block in response.blocks:
        text = clean_text(block.text)
        if not text:
            continue
        lines.append(
            ParsedBlock(
                kind="paragraph",
                text=text,
                page=page_number,
                bbox=block.bbox,
                meta={"confidence": block.confidence} if block.confidence is not None else {},
            )
        )
    if not lines:
        cleaned = clean_text(response.text)
        if not cleaned:
            return []
        return [
            ParsedBlock(kind="paragraph", text=part, page=page_number)
            for part in cleaned.split("\n\n")
            if part.strip()
        ]
    return _merge_ocr_lines(lines)


def _merge_ocr_lines(lines: list[ParsedBlock]) -> list[ParsedBlock]:
    """Merge OCR lines that sit close together into readable paragraphs."""
    positioned = [line for line in lines if line.bbox]
    if len(positioned) != len(lines) or not positioned:
        return lines

    heights = sorted(line.bbox[3] - line.bbox[1] for line in positioned if line.bbox)
    median_height = heights[len(heights) // 2] if heights else 12.0
    threshold = max(6.0, median_height * 0.9)

    merged: list[ParsedBlock] = []
    current: list[ParsedBlock] = []
    for line in lines:
        if current:
            previous = current[-1]
            assert previous.bbox and line.bbox
            gap = line.bbox[1] - previous.bbox[3]
            if gap > threshold:
                merged.append(_combine(current))
                current = []
        current.append(line)
    if current:
        merged.append(_combine(current))
    return merged


def _combine(lines: list[ParsedBlock]) -> ParsedBlock:
    text = "\n".join(line.text for line in lines)
    bboxes = [line.bbox for line in lines if line.bbox]
    bbox = None
    if bboxes:
        bbox = (
            min(box[0] for box in bboxes),
            min(box[1] for box in bboxes),
            max(box[2] for box in bboxes),
            max(box[3] for box in bboxes),
        )
    return ParsedBlock(kind="paragraph", text=text, page=lines[0].page, bbox=bbox)


def _replace_page_blocks(
    document: ParsedDocument, page_number: int, blocks: list[ParsedBlock]
) -> None:
    flat: list[tuple[ParsedSection, int]] = list(document.iter_sections())

    target: ParsedSection | None = None
    best_level = -1
    for section, level in flat:
        start = section.page_start or 1
        end = section.page_end or start
        if start <= page_number <= end and level > best_level:
            target = section
            best_level = level
    if target is None:
        if not document.sections:
            document.sections.append(
                ParsedSection(title=f"第 {page_number} 页", level=1, page_start=page_number, page_end=page_number)
            )
            target = document.sections[0]
        else:
            target = document.sections[0]

    for section, _ in flat:
        section.blocks = [block for block in section.blocks if block.page != page_number]
    target.blocks.extend(blocks)


def merge_blocks_for_reading(blocks: Iterable[ParsedBlock]) -> str:
    return "\n\n".join(block.text for block in blocks if block.text.strip())