"""Structure-aware chunking.

Chunks never split a semantic unit unless it exceeds the hard maximum.  Parent
context is represented by the owning section, so retrieval can expand to the
full section and neighbouring blocks instead of embedding every level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.core.config import ChunkingSettings
from backend.core.documents.types import estimate_tokens

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;\n])")


@dataclass(slots=True)
class BlockInput:
    id: str
    text: str
    page: int | None = None
    kind: str = "paragraph"


@dataclass(slots=True)
class ChunkDraft:
    heading: str
    text: str
    token_count: int
    page_start: int | None
    page_end: int | None
    block_ids: list[str] = field(default_factory=list)


def chunk_blocks(
    heading: str,
    blocks: list[BlockInput],
    settings: ChunkingSettings,
) -> list[ChunkDraft]:
    clean_blocks = [block for block in blocks if block.text.strip()]
    if not clean_blocks:
        return []

    chunks: list[ChunkDraft] = []
    buffer: list[BlockInput] = []
    buffer_tokens = 0

    def flush() -> None:
        nonlocal buffer, buffer_tokens
        if not buffer:
            return
        chunks.append(_draft(heading, buffer))
        buffer = []
        buffer_tokens = 0

    for block in clean_blocks:
        tokens = estimate_tokens(block.text)
        if tokens > settings.max_tokens:
            flush()
            for piece in _split_long(block.text, settings):
                chunks.append(
                    ChunkDraft(
                        heading=heading,
                        text=piece,
                        token_count=estimate_tokens(piece),
                        page_start=block.page,
                        page_end=block.page,
                        block_ids=[block.id],
                    )
                )
            continue

        if buffer and buffer_tokens + tokens > settings.max_tokens:
            flush()
        buffer.append(block)
        buffer_tokens += tokens + 1
        if buffer_tokens >= settings.target_tokens:
            flush()

    flush()
    return _merge_small(chunks, settings)


def _draft(heading: str, blocks: list[BlockInput]) -> ChunkDraft:
    text = "\n\n".join(block.text.strip() for block in blocks if block.text.strip())
    pages = [block.page for block in blocks if block.page is not None]
    return ChunkDraft(
        heading=heading,
        text=text,
        token_count=estimate_tokens(text),
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        block_ids=[block.id for block in blocks],
    )


def _split_long(text: str, settings: ChunkingSettings) -> list[str]:
    sentences = [part for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    if not sentences:
        sentences = [text]

    pieces: list[str] = []
    buffer = ""
    buffer_tokens = 0

    def flush() -> None:
        nonlocal buffer, buffer_tokens
        if buffer.strip():
            pieces.append(buffer.strip())
        buffer = ""
        buffer_tokens = 0

    for sentence in sentences:
        tokens = estimate_tokens(sentence)
        if tokens > settings.max_tokens:
            flush()
            limit = max(64, int(settings.max_tokens * 1.4))
            for start in range(0, len(sentence), limit):
                piece = sentence[start : start + limit].strip()
                if piece:
                    pieces.append(piece)
            continue
        if buffer and buffer_tokens + tokens > settings.target_tokens:
            flush()
        buffer += sentence
        buffer_tokens += tokens
    flush()
    return pieces


def _merge_small(chunks: list[ChunkDraft], settings: ChunkingSettings) -> list[ChunkDraft]:
    if len(chunks) < 2 or settings.min_tokens <= 0:
        return chunks
    merged: list[ChunkDraft] = []
    for chunk in chunks:
        if (
            merged
            and chunk.token_count < settings.min_tokens
            and merged[-1].token_count + chunk.token_count <= settings.max_tokens
            and merged[-1].page_end is not None
            and chunk.page_start is not None
            and chunk.page_start - merged[-1].page_end <= 1
        ):
            previous = merged[-1]
            previous.text = f"{previous.text}\n\n{chunk.text}"
            previous.token_count = estimate_tokens(previous.text)
            previous.page_end = max(previous.page_end or 0, chunk.page_end or 0)
            previous.block_ids.extend(chunk.block_ids)
            continue
        merged.append(chunk)
    return merged