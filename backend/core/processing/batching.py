"""Batch planning for full-document reading.

A batch is a contiguous slice of sections/blocks from one document, sized by
the LLM context budget.  Segments reference positions in the parsed snapshot
so a batch can be rebuilt deterministically after a restart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.core.documents.types import ParsedDocument, estimate_tokens


@dataclass(slots=True)
class BatchDraft:
    batch_index: int
    document_id: str
    label: str
    segments: list[dict[str, int]] = field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    tokens: int = 0


def build_batches(
    parsed: ParsedDocument,
    *,
    document_id: str,
    document_title: str,
    target_tokens: int,
    max_tokens: int,
    start_index: int = 0,
) -> list[BatchDraft]:
    flat = list(parsed.iter_sections())
    target = max(400, target_tokens)
    hard_max = max(target, max_tokens)

    drafts: list[BatchDraft] = []
    current_segments: list[dict[str, int]] = []
    current_tokens = 0
    current_pages: list[int] = []
    current_title = ""

    def flush() -> None:
        nonlocal current_segments, current_tokens, current_pages, current_title
        if not current_segments:
            return
        pages = sorted(set(current_pages))
        drafts.append(
            BatchDraft(
                batch_index=start_index + len(drafts),
                document_id=document_id,
                label=f"{document_title} · {current_title}".strip(" ·"),
                segments=list(current_segments),
                page_start=pages[0] if pages else None,
                page_end=pages[-1] if pages else None,
                tokens=current_tokens,
            )
        )
        current_segments = []
        current_tokens = 0
        current_pages = []
        current_title = ""

    for section_index, (section, _depth) in enumerate(flat):
        blocks = [block for block in section.blocks if block.text.strip()]
        if not blocks:
            continue
        block_tokens = [estimate_tokens(block.text) for block in blocks]
        section_tokens = sum(block_tokens) + estimate_tokens(section.title)

        if section_tokens <= hard_max:
            if current_segments and current_tokens + section_tokens > hard_max:
                flush()
            current_segments.append(
                {"section_index": section_index, "block_start": 0, "block_end": len(blocks)}
            )
            current_tokens += section_tokens
            current_pages.extend(block.page for block in blocks if block.page)
            if not current_title:
                current_title = section.title
            if current_tokens >= target:
                flush()
            continue

        # Oversized section: split on block boundaries.
        flush()
        group_start = 0
        group_tokens = 0
        for block_index, tokens in enumerate(block_tokens):
            if group_start < block_index and group_tokens + tokens > target:
                _append_segment_batch(
                    drafts,
                    start_index,
                    document_id,
                    document_title,
                    section.title,
                    section_index,
                    group_start,
                    block_index,
                    blocks,
                )
                group_start = block_index
                group_tokens = 0
            group_tokens += tokens
        if group_start < len(blocks):
            _append_segment_batch(
                drafts,
                start_index,
                document_id,
                document_title,
                section.title,
                section_index,
                group_start,
                len(blocks),
                blocks,
            )

    flush()
    return drafts


def _append_segment_batch(
    drafts: list[BatchDraft],
    start_index: int,
    document_id: str,
    document_title: str,
    section_title: str,
    section_index: int,
    block_start: int,
    block_end: int,
    blocks: list[Any],
) -> None:
    pages = sorted({block.page for block in blocks[block_start:block_end] if block.page})
    drafts.append(
        BatchDraft(
            batch_index=start_index + len(drafts),
            document_id=document_id,
            label=f"{document_title} · {section_title}",
            segments=[
                {
                    "section_index": section_index,
                    "block_start": block_start,
                    "block_end": block_end,
                }
            ],
            page_start=pages[0] if pages else None,
            page_end=pages[-1] if pages else None,
            tokens=sum(estimate_tokens(block.text) for block in blocks[block_start:block_end]),
        )
    )


def batch_text(parsed: ParsedDocument, segments: list[dict[str, int]]) -> str:
    flat = list(parsed.iter_sections())
    parts: list[str] = []
    for segment in segments:
        index = int(segment.get("section_index", -1))
        if not 0 <= index < len(flat):
            continue
        section = flat[index][0]
        blocks = section.blocks[int(segment.get("block_start", 0)) : int(segment.get("block_end", 0))]
        text = "\n\n".join(block.text for block in blocks if block.text.strip())
        if not text:
            continue
        pages = sorted({block.page for block in blocks if block.page})
        heading = f"## {section.title}" if section.title else ""
        if pages:
            suffix = f"（第 {pages[0]} 页）" if len(pages) == 1 else f"（第 {pages[0]}–{pages[-1]} 页）"
            heading += suffix
        parts.append(f"{heading}\n\n{text}" if heading else text)
    return "\n\n".join(parts)


def batch_page_range(parsed: ParsedDocument, segments: list[dict[str, int]]) -> tuple[int | None, int | None]:
    flat = list(parsed.iter_sections())
    pages: list[int] = []
    for segment in segments:
        index = int(segment.get("section_index", -1))
        if not 0 <= index < len(flat):
            continue
        section = flat[index][0]
        blocks = section.blocks[int(segment.get("block_start", 0)) : int(segment.get("block_end", 0))]
        pages.extend(block.page for block in blocks if block.page)
    if not pages:
        return None, None
    return min(pages), max(pages)