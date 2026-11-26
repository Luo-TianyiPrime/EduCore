"""Persisting a parsed document as a new version with chunks and FTS rows."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from backend.core.config import ChunkingSettings
from backend.core.documents.types import ParsedDocument, ParsedSection
from backend.core.indexing.chunker import BlockInput, chunk_blocks
from backend.storage.documents import DocumentRepo
from backend.storage.index_store import IndexStore, dump_bbox

logger = logging.getLogger(__name__)


class Indexer:
    def __init__(
        self,
        *,
        cache_dir: Path,
        documents: DocumentRepo,
        index: IndexStore,
        chunking: ChunkingSettings,
    ) -> None:
        self.cache_dir = cache_dir
        self.documents = documents
        self.index = index
        self.chunking = chunking

    def persist(
        self,
        *,
        document: dict[str, Any],
        parsed: ParsedDocument,
        content_hash: str,
        status: str,
    ) -> dict[str, Any]:
        document_id = document["id"]
        version_id = uuid.uuid4().hex[:16]

        section_rows: list[tuple[Any, ...]] = []
        block_rows: list[tuple[Any, ...]] = []
        chunk_rows: list[tuple[Any, ...]] = []
        chunk_counter = 0
        section_counter = 0
        block_counter = 0

        def walk(nodes: list[ParsedSection], parent_id: str | None) -> None:
            nonlocal chunk_counter, section_counter, block_counter
            for section in nodes:
                section_id = uuid.uuid4().hex[:16]
                section_rows.append(
                    (
                        section_id,
                        version_id,
                        parent_id,
                        section_counter,
                        section.level,
                        section.title,
                        section.page_start,
                        section.page_end,
                    )
                )
                section_counter += 1

                block_inputs: list[BlockInput] = []
                for block in section.blocks:
                    if not block.text.strip():
                        continue
                    block_id = uuid.uuid4().hex[:16]
                    block_rows.append(
                        (
                            block_id,
                            version_id,
                            section_id,
                            block_counter,
                            block.kind,
                            block.text,
                            block.page,
                            dump_bbox(block.bbox),
                            json.dumps(block.meta, ensure_ascii=False) if block.meta else None,
                        )
                    )
                    block_counter += 1
                    block_inputs.append(
                        BlockInput(id=block_id, text=block.text, page=block.page, kind=block.kind)
                    )

                for chunk in chunk_blocks(section.title, block_inputs, self.chunking):
                    chunk_rows.append(
                        (
                            uuid.uuid4().hex[:16],
                            version_id,
                            section_id,
                            chunk_counter,
                            chunk.heading,
                            chunk.text,
                            chunk.token_count,
                            chunk.page_start,
                            chunk.page_end,
                            json.dumps(chunk.block_ids),
                        )
                    )
                    chunk_counter += 1

                walk(section.children, section_id)

        walk(parsed.sections, None)

        parsed_path = self._save_parsed(document_id, version_id, parsed)

        version = self.documents.create_version(
            document_id=document_id,
            content_hash=content_hash,
            parsed_path=str(parsed_path),
            page_count=parsed.page_count,
            block_count=len(block_rows),
            chunk_count=len(chunk_rows),
            version_id=version_id,
        )

        self.index.insert_sections(section_rows)
        self.index.insert_blocks(block_rows)
        self.index.insert_chunks(chunk_rows)
        self.index.index_version_fts(version_id)

        self.documents.set_status(
            document_id,
            status,
            error=None,
            title=parsed.title,
            page_count=parsed.page_count,
            content_hash=content_hash,
        )

        stale = self.documents.delete_old_versions(document_id, keep=3)
        for version_id_stale in stale:
            self.index.delete_version_fts(version_id_stale)

        return {
            "version": version,
            "sections": len(section_rows),
            "blocks": len(block_rows),
            "chunks": len(chunk_rows),
        }

    def _save_parsed(self, document_id: str, version_id: str, parsed: ParsedDocument) -> Path:
        directory = self.cache_dir / "parse" / document_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{version_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(parsed.to_dict(), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp.replace(path)
        return path


def has_readable_content(parsed: ParsedDocument) -> bool:
    return any(block.text.strip() for _section, block in parsed.iter_blocks()) or any(
        page.has_text for page in parsed.pages
    )