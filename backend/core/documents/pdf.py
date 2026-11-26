"""PDF parsing: text layer, page labels, bookmarks, coordinates.

pypdfium2 provides fast page text and rendering; pdfplumber supplies word-level
coordinates that are grouped into lines and paragraphs.  When pdfplumber cannot
read a page we fall back to plain paragraph splitting without coordinates, and
the document records a warning instead of inventing positions.
"""

from __future__ import annotations

import logging
import re
from io import BytesIO
from pathlib import Path

from backend.core.documents.types import (
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    ParsedSection,
    ParseError,
    clean_text,
)

logger = logging.getLogger(__name__)

_CJK_GAP = re.compile(r"(?<=[\u3400-\u4dbf\u4e00-\u9fff])\s+(?=[\u4dbf\u4e00-\u9fff])")
_CJK_BEFORE_PUNCT = re.compile(r"\s+(?=[，。、；：？！）》」』】])")


def render_page_png(path: Path, page_number: int, *, scale: float = 2.0) -> bytes:
    """Render a 1-based page to PNG bytes."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - dependency present in installs
        raise ParseError("缺少 pypdfium2，无法渲染 PDF 页面") from exc

    document = pdfium.PdfDocument(str(path))
    try:
        if not 1 <= page_number <= len(document):
            raise ParseError(f"页码超出范围: {page_number}")
        page = document[page_number - 1]
        try:
            bitmap = page.render(scale=scale)
            return _pil_png(bitmap)
        finally:
            page.close()
    finally:
        document.close()


def _pil_png(bitmap) -> bytes:
    image = bitmap.to_pil()
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def parse_pdf(path: Path) -> ParsedDocument:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover
        raise ParseError("缺少 pypdfium2，无法解析 PDF") from exc

    warnings: list[str] = []
    try:
        document = pdfium.PdfDocument(str(path))
    except Exception as exc:
        raise ParseError("PDF 无法打开", detail=str(exc)) from exc

    pages: list[ParsedPage] = []
    raw_texts: dict[int, str] = {}
    metadata_title: str | None = None
    try:
        if hasattr(document, "get_metadata_value"):
            try:
                value = document.get_metadata_value("Title")
                metadata_title = clean_text(value) if isinstance(value, str) else None
            except Exception:
                metadata_title = None
        for index in range(len(document)):
            page = document[index]
            try:
                text = ""
                try:
                    textpage = page.get_textpage()
                    try:
                        text = textpage.get_text_range() or ""
                    finally:
                        textpage.close()
                except Exception as exc:  # pragma: no cover - malformed pages
                    warnings.append(f"第 {index + 1} 页文本层读取失败: {exc}")
                width, height = page.get_size()
                label = None
                if hasattr(page, "get_label"):
                    try:
                        label = page.get_label() or None
                    except Exception:
                        label = None
                cleaned = clean_text(text)
                raw_texts[index + 1] = cleaned
                pages.append(
                    ParsedPage(
                        number=index + 1,
                        text=cleaned,
                        has_text=len(cleaned) >= 12,
                        label=label,
                        width=float(width),
                        height=float(height),
                    )
                )
            finally:
                page.close()
    finally:
        document.close()

    toc = _read_toc(path)
    sections = _sections_from_toc(toc, len(pages))
    blocks_by_page = _extract_page_blocks(path, raw_texts, warnings)

    if not sections:
        sections = [
            ParsedSection(
                title=f"第 {page.number} 页",
                level=1,
                page_start=page.number,
                page_end=page.number,
            )
            for page in pages
        ]

    _assign_blocks(sections, blocks_by_page)

    if metadata_title and metadata_title.strip().lower() in {"untitled", "无标题"}:
        metadata_title = None
    title = metadata_title or path.stem
    meta = {
        "page_labels": [page.label for page in pages],
        "toc": [{"level": level, "title": title, "page": page} for level, title, page in toc],
    }
    document_model = ParsedDocument(
        title=title,
        format="pdf",
        pages=pages,
        sections=sections,
        warnings=warnings,
        meta=meta,
    )
    if document_model.needs_ocr_pages:
        warnings.append(
            f"{len(document_model.needs_ocr_pages)} 页未检测到有效文本层，可配置 OCR 后重新识别。"
        )
    return document_model


# --------------------------------------------------------------------------
# Table of contents
# --------------------------------------------------------------------------


def _read_toc(path: Path) -> list[tuple[int, str, int | None]]:
    try:
        import pypdfium2 as pdfium
    except ImportError:  # pragma: no cover
        return []

    document = pdfium.PdfDocument(str(path))
    entries: list[tuple[int, str, int | None]] = []
    try:
        for bookmark in document.get_toc():
            page: int | None = None
            try:
                destination = bookmark.get_dest()
                if destination is not None:
                    page_index = destination.get_index()
                    if page_index is not None:
                        page = int(page_index) + 1
            except Exception:
                page = None
            title = clean_text(bookmark.get_title() if hasattr(bookmark, "get_title") else "")
            entries.append((int(bookmark.level) + 1, title, page))
    except Exception as exc:  # pragma: no cover - malformed outlines
        logger.warning("读取 PDF 目录失败 %s: %s", path, exc)
        return []
    finally:
        document.close()
    return entries


def _sections_from_toc(
    toc: list[tuple[int, str, int | None]], page_count: int
) -> list[ParsedSection]:
    if not toc:
        return []

    # Resolve missing destinations by carrying forward the next known page.
    entries = list(toc)
    resolved: list[tuple[int, str, int | None]] = []
    next_known: int | None = None
    for level, title, page in reversed(entries):
        if page is None:
            page = next_known
        else:
            next_known = page
        resolved.append((level, title, page))
    resolved.reverse()

    flat: list[ParsedSection] = []
    for level, title, page in resolved:
        if page is None:
            page = 1
        flat.append(ParsedSection(title=title or "未命名章节", level=level, page_start=page))

    for index, section in enumerate(flat):
        if index + 1 < len(flat):
            section.page_end = max(section.page_start or 1, (flat[index + 1].page_start or 1) - 1)
        else:
            section.page_end = page_count
        if section.page_end is None or section.page_end < (section.page_start or 1):
            section.page_end = section.page_start

    roots: list[ParsedSection] = []
    stack: list[ParsedSection] = []
    for section in flat:
        while stack and stack[-1].level >= section.level:
            stack.pop()
        if stack:
            stack[-1].children.append(section)
        else:
            roots.append(section)
        stack.append(section)
    return roots


# --------------------------------------------------------------------------
# Blocks
# --------------------------------------------------------------------------


def _extract_page_blocks(
    path: Path,
    raw_texts: dict[int, str],
    warnings: list[str],
) -> dict[int, list[ParsedBlock]]:
    try:
        blocks = _blocks_with_pdfplumber(path)
        if blocks:
            return blocks
    except Exception as exc:
        logger.warning("pdfplumber 提取坐标失败 %s: %s", path, exc)
        warnings.append("未能提取 PDF 文本坐标，已回退为按页文本分段。")

    return {
        page: _paragraph_blocks(text, page)
        for page, text in raw_texts.items()
        if text.strip()
    }


def _blocks_with_pdfplumber(path: Path) -> dict[int, list[ParsedBlock]]:
    import pdfplumber

    result: dict[int, list[ParsedBlock]] = {}
    with pdfplumber.open(str(path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(keep_blank_chars=False, use_text_flow=True)
            if not words:
                continue
            lines = _group_words_into_lines(words)
            paragraphs = _group_lines_into_paragraphs(lines)
            blocks: list[ParsedBlock] = []
            for text, bbox in paragraphs:
                if text.strip():
                    blocks.append(ParsedBlock(kind="paragraph", text=text, page=page_number, bbox=bbox))
            if blocks:
                result[page_number] = blocks
    return result


def _group_words_into_lines(words: list[dict]) -> list[dict]:
    tolerance = 3.0
    ordered = sorted(words, key=lambda word: (round(float(word["top"]), 1), float(word["x0"])))
    lines: list[dict] = []
    for word in ordered:
        top = float(word["top"])
        if lines and abs(top - lines[-1]["top"]) <= tolerance:
            line = lines[-1]
            line["words"].append(word)
            line["top"] = min(line["top"], top)
            line["bottom"] = max(line["bottom"], float(word["bottom"]))
        else:
            lines.append(
                {
                    "top": top,
                    "bottom": float(word["bottom"]),
                    "words": [word],
                }
            )
    for line in lines:
        line["words"].sort(key=lambda word: float(word["x0"]))
        raw = " ".join(str(word["text"]) for word in line["words"])
        text = _CJK_GAP.sub("", raw)
        text = _CJK_BEFORE_PUNCT.sub("", text)
        line["text"] = clean_text(text)
        line["x0"] = min(float(word["x0"]) for word in line["words"])
        line["x1"] = max(float(word["x1"]) for word in line["words"])
    return lines


def _group_lines_into_paragraphs(lines: list[dict]) -> list[tuple[str, tuple[float, float, float, float]]]:
    paragraphs: list[tuple[str, tuple[float, float, float, float]]] = []
    current: list[dict] = []
    heights = [max(1.0, line["bottom"] - line["top"]) for line in lines]
    heights.sort()
    median_height = heights[len(heights) // 2] if heights else 12.0
    gap_threshold = median_height * 1.45

    def flush() -> None:
        if not current:
            return
        text = "\n".join(line["text"] for line in current if line["text"]).strip()
        if not text:
            current.clear()
            return
        bbox = (
            min(line["x0"] for line in current),
            min(line["top"] for line in current),
            max(line["x1"] for line in current),
            max(line["bottom"] for line in current),
        )
        paragraphs.append((text, bbox))
        current.clear()

    last_bottom: float | None = None
    for line in lines:
        if last_bottom is not None and (line["top"] - last_bottom) > gap_threshold:
            flush()
        current.append(line)
        last_bottom = line["bottom"]
    flush()
    return paragraphs


def _paragraph_blocks(text: str, page: int) -> list[ParsedBlock]:
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
    if not chunks:
        chunks = [line.strip() for line in text.splitlines() if line.strip()]
    return [ParsedBlock(kind="paragraph", text=chunk, page=page) for chunk in chunks]


def _assign_blocks(
    sections: list[ParsedSection],
    blocks_by_page: dict[int, list[ParsedBlock]],
) -> None:
    flat: list[tuple[ParsedSection, int]] = []

    def walk(nodes: list[ParsedSection]) -> None:
        for node in nodes:
            flat.append((node, node.level))
            walk(node.children)

    walk(sections)

    for page, blocks in blocks_by_page.items():
        target: ParsedSection | None = None
        best_level = -1
        for section, level in flat:
            start = section.page_start or 1
            end = section.page_end or start
            if start <= page <= end and level > best_level:
                target = section
                best_level = level
        if target is None:
            if not sections:
                continue
            target = sections[0]
        target.blocks.extend(blocks)