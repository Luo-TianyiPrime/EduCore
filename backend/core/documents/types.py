"""Unified internal document model.

Parsers produce :class:`ParsedDocument`; the indexing layer turns it into
sections, blocks and chunks.  This structure is deliberately independent from
any provider or file format.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_SPACE_RE = re.compile(r"[ \t\u00a0]+")


def clean_text(value: str) -> str:
    """Normalise horizontal whitespace while keeping paragraph breaks."""
    if not value:
        return ""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _SPACE_RE.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def estimate_tokens(text: str) -> int:
    """Cheap, model-independent token estimate.

    Chinese characters count roughly one token each, Latin words ~1.3 tokens.
    The value is used for chunk sizing only; exact tokenizers stay in the model
    layer.  Numbers are intentionally conservative (slightly over-estimate).
    """
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    words = len(_LATIN_WORD_RE.findall(text))
    other = len(text) - cjk
    return max(1, int(cjk * 1.0 + words * 1.3 + other * 0.3))


@dataclass(slots=True)
class ParsedBlock:
    kind: str
    text: str
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedSection:
    title: str
    level: int = 1
    page_start: int | None = None
    page_end: int | None = None
    blocks: list[ParsedBlock] = field(default_factory=list)
    children: list[ParsedSection] = field(default_factory=list)


@dataclass(slots=True)
class ParsedPage:
    number: int
    text: str = ""
    has_text: bool = False
    label: str | None = None
    width: float | None = None
    height: float | None = None
    ocr_used: bool = False


@dataclass(slots=True)
class ParsedDocument:
    title: str
    format: str
    pages: list[ParsedPage] = field(default_factory=list)
    sections: list[ParsedSection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def needs_ocr_pages(self) -> list[int]:
        return [page.number for page in self.pages if not page.has_text]

    def iter_sections(self, depth: int = 1) -> Iterator[tuple[ParsedSection, int]]:
        for section in self.sections:
            yield section, depth
            yield from _walk(section, depth + 1)

    def iter_blocks(self) -> Iterator[tuple[ParsedSection, ParsedBlock]]:
        for section, _ in self.iter_sections():
            for block in section.blocks:
                yield section, block

    def plain_text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "format": self.format,
            "warnings": self.warnings,
            "meta": self.meta,
            "pages": [
                {
                    "number": page.number,
                    "text": page.text,
                    "has_text": page.has_text,
                    "label": page.label,
                    "width": page.width,
                    "height": page.height,
                    "ocr_used": page.ocr_used,
                }
                for page in self.pages
            ],
            "sections": [_section_to_dict(section) for section in self.sections],
        }


def _walk(section: ParsedSection, depth: int) -> Iterator[tuple[ParsedSection, int]]:
    for child in section.children:
        yield child, depth
        yield from _walk(child, depth + 1)


def _section_to_dict(section: ParsedSection) -> dict[str, Any]:
    return {
        "title": section.title,
        "level": section.level,
        "page_start": section.page_start,
        "page_end": section.page_end,
        "blocks": [
            {
                "kind": block.kind,
                "text": block.text,
                "page": block.page,
                "bbox": list(block.bbox) if block.bbox else None,
                "meta": block.meta,
            }
            for block in section.blocks
        ],
        "children": [_section_to_dict(child) for child in section.children],
    }


def document_from_dict(data: dict[str, Any]) -> ParsedDocument:
    """Rehydrate a snapshot written by :meth:`ParsedDocument.to_dict`."""
    pages = [
        ParsedPage(
            number=int(item.get("number") or index + 1),
            text=str(item.get("text") or ""),
            has_text=bool(item.get("has_text")),
            label=item.get("label"),
            width=item.get("width"),
            height=item.get("height"),
            ocr_used=bool(item.get("ocr_used")),
        )
        for index, item in enumerate(data.get("pages") or [])
    ]
    sections = [_section_from_dict(item) for item in data.get("sections") or []]
    return ParsedDocument(
        title=str(data.get("title") or ""),
        format=str(data.get("format") or ""),
        pages=pages,
        sections=sections,
        warnings=list(data.get("warnings") or []),
        meta=dict(data.get("meta") or {}),
    )


def _section_from_dict(data: dict[str, Any]) -> ParsedSection:
    blocks = []
    for item in data.get("blocks") or []:
        bbox = item.get("bbox")
        blocks.append(
            ParsedBlock(
                kind=str(item.get("kind") or "paragraph"),
                text=str(item.get("text") or ""),
                page=item.get("page"),
                bbox=tuple(float(value) for value in bbox) if bbox else None,
                meta=dict(item.get("meta") or {}),
            )
        )
    return ParsedSection(
        title=str(data.get("title") or ""),
        level=int(data.get("level") or 1),
        page_start=data.get("page_start"),
        page_end=data.get("page_end"),
        blocks=blocks,
        children=[_section_from_dict(child) for child in data.get("children") or []],
    )


class ParseError(Exception):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail