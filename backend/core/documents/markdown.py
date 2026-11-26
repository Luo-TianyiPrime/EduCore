"""Markdown parsing with heading hierarchy, lists, tables, code and images.

The parser keeps the author's heading structure as sections.  Content before the
first heading becomes a "正文" section instead of being dropped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

from backend.core.documents.types import (
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
    ParsedSection,
    ParseError,
    clean_text,
)

_HEADING_LEVEL = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


def parse_markdown(path: Path) -> ParsedDocument:
    try:
        source = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            source = path.read_text(encoding="gb18030")
        except (UnicodeDecodeError, OSError) as exc:
            raise ParseError("Markdown 文件编码无法识别", detail=str(exc)) from exc
    except OSError as exc:
        raise ParseError("Markdown 文件读取失败", detail=str(exc)) from exc

    md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    tokens = md.parse(source)

    roots: list[ParsedSection] = []
    stack: list[ParsedSection] = []
    title: str | None = None
    images: list[dict[str, Any]] = []

    def current_section() -> ParsedSection:
        nonlocal stack
        if not stack:
            implicit = ParsedSection(title="正文", level=0)
            roots.append(implicit)
            stack = [implicit]
        return stack[-1]

    index = 0
    while index < len(tokens):
        token = tokens[index]

        if token.type == "heading_open":
            level = _HEADING_LEVEL.get(token.tag, 1)
            inline = tokens[index + 1] if index + 1 < len(tokens) else None
            heading_text = clean_text(_inline_text(inline))
            _collect_images(inline, images)
            while stack and stack[-1].level >= level:
                stack.pop()
            section = ParsedSection(title=heading_text or "未命名章节", level=level)
            if stack:
                stack[-1].children.append(section)
            else:
                roots.append(section)
            stack.append(section)
            if title is None and level == 1 and heading_text:
                title = heading_text
            index += 2
            continue

        if token.type == "paragraph_open":
            inline = tokens[index + 1] if index + 1 < len(tokens) else None
            _collect_images(inline, images)
            text = clean_text(_inline_text(inline))
            if text:
                current_section().blocks.append(ParsedBlock(kind="paragraph", text=text))
            index += 2
            continue

        if token.type == "fence" or token.type == "code_block":
            text = clean_text(token.content)
            if text:
                current_section().blocks.append(
                    ParsedBlock(kind="code", text=text, meta={"info": token.info or ""})
                )
            index += 1
            continue

        if token.type in {"bullet_list_open", "ordered_list_open"}:
            order = str(token.markup or "-")
            index += 1
            while index < len(tokens) and tokens[index].type not in {
                "bullet_list_close",
                "ordered_list_close",
            }:
                if tokens[index].type == "list_item_open":
                    inline = _first_inline(tokens, index)
                    _collect_images(inline, images)
                    text = clean_text(_inline_text(inline))
                    if text:
                        current_section().blocks.append(
                            ParsedBlock(kind="list_item", text=text, meta={"marker": order})
                        )
                index += 1
            index += 1
            continue

        if token.type == "table_open":
            cells: list[str] = []
            index += 1
            while index < len(tokens) and tokens[index].type != "table_close":
                if tokens[index].type == "inline":
                    text = clean_text(_inline_text(tokens[index]))
                    if text:
                        cells.append(text)
                index += 1
            index += 1
            if cells:
                current_section().blocks.append(
                    ParsedBlock(kind="table", text=" | ".join(cells), meta={"cells": cells})
                )
            continue

        if token.type == "blockquote_open":
            inline = _first_inline(tokens, index)
            text = clean_text(_inline_text(inline))
            if text:
                current_section().blocks.append(ParsedBlock(kind="blockquote", text=text))
            index += 1
            continue

        if token.type == "html_block":
            text = clean_text(token.content)
            if text:
                current_section().blocks.append(ParsedBlock(kind="html", text=text))
            index += 1
            continue

        index += 1

    # Drop the implicit section when headings already cover everything.
    roots = [section for section in roots if section.blocks or section.children or section.level > 0]
    if not roots:
        roots = [ParsedSection(title="正文", level=1, blocks=[ParsedBlock("paragraph", "（空文档）")])]

    pages = [
        ParsedPage(
            number=1,
            text=clean_text(source),
            has_text=bool(source.strip()),
            label="1",
        )
    ]

    return ParsedDocument(
        title=title or path.stem,
        format="markdown",
        pages=pages,
        sections=roots,
        meta={"images": images, "headings": [s.title for s in roots]},
    )


def _inline_text(token: Token | None) -> str:
    if token is None:
        return ""
    if token.type == "inline":
        return token.content
    return token.content or ""


def _first_inline(tokens: list[Token], start: int) -> Token | None:
    depth = 0
    for index in range(start, min(start + 12, len(tokens))):
        token = tokens[index]
        if token.type.endswith("_open"):
            depth += 1
        elif token.type.endswith("_close"):
            depth -= 1
        elif token.type == "inline":
            return token
        if depth <= 0 and index > start and token.type.endswith("_close"):
            break
    return None


def _collect_images(token: Token | None, images: list[dict[str, Any]]) -> None:
    if token is None or token.type != "inline":
        return
    for child in token.children or []:
        if child.type == "image":
            images.append({"src": child.attrs.get("src"), "alt": child.content, "title": child.attrs.get("title")})