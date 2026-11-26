"""Structured artifact document model and Markdown conversion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

_HEADING = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_CITATION = re.compile(r"（《([^》]{1,120})》(?:[^）]{0,80}?第\s*(\d+)(?:[–-]\d+)?\s*页)?[^）]*）")
_TOTAL = re.compile(r"总分\s*[:：]?\s*(\d{1,3})\s*分?")


@dataclass(slots=True)
class ArtifactBlock:
    kind: str
    text: str = ""
    level: int = 1
    items: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ArtifactSection:
    title: str
    level: int
    blocks: list[ArtifactBlock] = field(default_factory=list)
    children: list[ArtifactSection] = field(default_factory=list)


@dataclass(slots=True)
class ArtifactDocument:
    title: str
    sections: list[ArtifactSection]
    sources: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "meta": self.meta,
            "sources": self.sources,
            "sections": [_section_to_dict(section) for section in self.sections],
        }

    def iter_sections(self):
        for section in self.sections:
            yield section
            yield from _walk(section)

    def plain_text(self) -> str:
        parts: list[str] = [self.title]
        for section in self.iter_sections():
            parts.append(section.title)
            for block in section.blocks:
                if block.text:
                    parts.append(block.text)
                parts.extend(block.items)
                for row in block.rows:
                    parts.append(" | ".join(row))
        return "\n".join(part for part in parts if part)


def _walk(section: ArtifactSection):
    for child in section.children:
        yield child
        yield from _walk(child)


def _section_to_dict(section: ArtifactSection) -> dict[str, Any]:
    return {
        "title": section.title,
        "level": section.level,
        "blocks": [
            {
                "kind": block.kind,
                "text": block.text,
                "level": block.level,
                "items": block.items,
                "rows": block.rows,
                "meta": block.meta,
            }
            for block in section.blocks
        ],
        "children": [_section_to_dict(child) for child in section.children],
    }


def parse_artifact(markdown: str, *, fallback_title: str = "未命名成果") -> ArtifactDocument:
    md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    tokens = md.parse(markdown or "")
    roots: list[ArtifactSection] = []
    stack: list[ArtifactSection] = []
    title: str | None = None
    sources: list[dict[str, Any]] = []

    def ensure_section() -> ArtifactSection:
        nonlocal stack
        if not stack:
            implicit = ArtifactSection(title="正文", level=1)
            roots.append(implicit)
            stack = [implicit]
        return stack[-1]

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type == "heading_open":
            level = _HEADING.get(token.tag, 1)
            inline = tokens[index + 1] if index + 1 < len(tokens) else None
            text = _inline_text(inline)
            if level == 1 and title is None and not roots:
                title = text or title
                index += 2
                continue
            while stack and stack[-1].level >= level:
                stack.pop()
            section = ArtifactSection(title=text or "未命名章节", level=level)
            if stack:
                stack[-1].children.append(section)
            else:
                roots.append(section)
            stack.append(section)
            if title is None and level == 1 and text:
                title = text
            index += 2
            continue

        if token.type == "paragraph_open":
            inline = tokens[index + 1] if index + 1 < len(tokens) else None
            text = _inline_text(inline)
            if text:
                ensure_section().blocks.append(ArtifactBlock("paragraph", text=text))
            index += 2
            continue

        if token.type in {"bullet_list_open", "ordered_list_open"}:
            ordered = token.type == "ordered_list_open"
            items: list[str] = []
            index += 1
            while index < len(tokens) and tokens[index].type not in {
                "bullet_list_close",
                "ordered_list_close",
            }:
                if tokens[index].type == "inline" and items is not None:
                    text = _inline_text(tokens[index])
                    if text:
                        items.append(text)
                index += 1
            index += 1
            if items:
                ensure_section().blocks.append(
                    ArtifactBlock("list", items=items, meta={"ordered": ordered})
                )
            continue

        if token.type == "table_open":
            rows: list[list[str]] = []
            row: list[str] | None = None
            index += 1
            while index < len(tokens) and tokens[index].type != "table_close":
                if tokens[index].type == "tr_open":
                    row = []
                elif tokens[index].type == "tr_close":
                    if row:
                        rows.append(row)
                    row = None
                elif tokens[index].type == "inline":
                    text = _inline_text(tokens[index])
                    if row is not None and text:
                        row.append(text)
                index += 1
            index += 1
            if rows:
                ensure_section().blocks.append(ArtifactBlock("table", rows=rows))
            continue

        if token.type in {"fence", "code_block"}:
            text = (token.content or "").strip("\n")
            if text:
                ensure_section().blocks.append(
                    ArtifactBlock("code", text=text, meta={"info": token.info or ""})
                )
            index += 1
            continue

        if token.type == "blockquote_open":
            texts: list[str] = []
            index += 1
            while index < len(tokens) and tokens[index].type != "blockquote_close":
                if tokens[index].type == "inline":
                    texts.append(_inline_text(tokens[index]))
                index += 1
            index += 1
            text = "\n".join(item for item in texts if item)
            if text:
                ensure_section().blocks.append(ArtifactBlock("quote", text=text))
            continue

        if token.type == "hr":
            ensure_section().blocks.append(ArtifactBlock("separator"))
            index += 1
            continue

        index += 1

    # Collect a trailing "来源" section into structured sources.
    for section in list(_all_sections(roots)):
        if section.title.strip() in {"来源", "参考来源", "参考资料", "Sources"}:
            for block in section.blocks:
                texts = block.items or ([block.text] if block.text else [])
                for text in texts:
                    sources.append({"text": text})

    document = ArtifactDocument(
        title=title or fallback_title,
        sections=roots,
        sources=sources,
    )
    if not roots:
        document.sections = [
            ArtifactSection(
                title="正文",
                level=1,
                blocks=[ArtifactBlock("paragraph", text=markdown.strip() or "（空成果）")],
            )
        ]
    return document


def _all_sections(roots: list[ArtifactSection]):
    for root in roots:
        yield root
        yield from _walk(root)


def _inline_text(token: Token | None) -> str:
    if token is None:
        return ""
    if token.type == "inline":
        return token.content.strip()
    return (token.content or "").strip()


def citations(markdown: str) -> list[dict[str, Any]]:
    result = []
    for match in _CITATION.finditer(markdown or ""):
        result.append({"document": match.group(1), "page": int(match.group(2)) if match.group(2) else None})
    return result


def strip_citations(markdown: str) -> str:
    return _CITATION.sub("", markdown or "")


def find_total_score(markdown: str) -> list[int]:
    return [int(match.group(1)) for match in _TOTAL.finditer(markdown or "")]


def replace_section(markdown: str, heading: str, new_content: str) -> str:
    lines = (markdown or "").splitlines()
    target = heading.strip()
    start = None
    level = 0
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if match and match.group(2).strip() == target:
            start = index
            level = len(match.group(1))
            break
    if start is None:
        raise ValueError(f"未找到章节：{heading}")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.match(r"^(#{1,6})\s+", lines[index].strip()):
            end = index
            break
    replacement = new_content.strip("\n")
    if not re.match(r"^#{1,6}\s+" + re.escape(target), replacement.splitlines()[0] if replacement else ""):
        replacement = f"{'#' * level} {target}\n\n{replacement}"
    new_lines = lines[:start] + replacement.splitlines() + [""] + lines[end:]
    return "\n".join(new_lines).strip() + "\n"

def extract_section(markdown: str, heading: str) -> tuple[int, list[str]]:
    """Return the heading level and lines of one section (heading included)."""
    lines = (markdown or "").splitlines()
    target = heading.strip()
    start = None
    level = 0
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if match and match.group(2).strip() == target:
            start = index
            level = len(match.group(1))
            break
    if start is None:
        raise ValueError(f"未找到章节：{heading}")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = re.match(r"^(#{1,6})\s+", lines[index].strip())
        if match and len(match.group(1)) <= level:
            end = index
            break
    return level, lines[start:end]
