"""Folder scanning, incremental parsing and indexing jobs.

One :class:`IndexService` instance lives on the application runtime.  Jobs run
as background asyncio tasks; progress is persisted in SQLite so the UI can poll
and restart safely.  CPU-heavy parsing runs in worker threads, and OCR calls go
through the shared model gateway with bounded concurrency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.config import AppConfig
from backend.core.documents.hashing import sha256_file
from backend.core.documents.ocr.service import OCRService, ocr_error_message
from backend.core.documents.parser import apply_ocr_results, detect_format, parse_file
from backend.core.documents.types import ParsedDocument, ParseError, document_from_dict
from backend.core.indexing.embeddings import EmbeddingService
from backend.core.indexing.indexer import Indexer, has_readable_content
from backend.core.models.gateway import ModelGateway
from backend.core.models.types import ModelError, ModelNotConfigured, OCRResponse
from backend.platform.paths import DataPaths
from backend.storage.db import Database
from backend.storage.documents import DocumentRepo
from backend.storage.index_store import IndexStore
from backend.storage.jobs import JobRepo
from backend.storage.ocr_cache import OcrCacheRepo
from backend.storage.sources import SourceRepo

logger = logging.getLogger(__name__)

SKIP_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "$RECYCLE.BIN",
    "System Volume Information",
}
MAX_FILE_SIZE = 200 * 1024 * 1024
OCR_PAGE_CONCURRENCY = 2


@dataclass(slots=True)
class ScanCandidate:
    path: Path
    rel_path: str
    size: int
    mtime: float
    format: str


class IndexService:
    def __init__(
        self,
        *,
        db: Database,
        paths: DataPaths,
        gateway: ModelGateway,
        config_provider: Callable[[], AppConfig],
        embeddings: EmbeddingService | None = None,
    ) -> None:
        self.db = db
        self.paths = paths
        self.gateway = gateway
        self.config_provider = config_provider
        self.embeddings = embeddings

        self.sources = SourceRepo(db)
        self.documents = DocumentRepo(db)
        self.index = IndexStore(db)
        self.jobs = JobRepo(db)
        self.ocr = OCRService(gateway, OcrCacheRepo(db))

        self._tasks: dict[str, asyncio.Task] = {}
        self._parse_semaphore = asyncio.Semaphore(2)

    # -- sources -----------------------------------------------------------
    def _normalise_source(self, raw: str) -> Path:
        path = Path(raw).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"路径不存在或不可访问: {raw}") from exc
        if not resolved.is_dir():
            raise ValueError("请选择一个文件夹，而不是单个文件")
        data_root = self.paths.root.resolve()
        if resolved == data_root or data_root in resolved.parents:
            raise ValueError("不能把 EduCore 数据目录本身作为资料文件夹")
        return resolved

    def add_source(self, raw_path: str, recursive: bool = True) -> dict[str, Any]:
        resolved = self._normalise_source(raw_path)
        existing = self.sources.get_by_path(str(resolved))
        if existing:
            raise ValueError("该文件夹已添加")
        return self.sources.add(str(resolved), recursive)

    def add_file(self, raw_path: str) -> dict[str, Any]:
        path = Path(raw_path).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"文件不存在或不可访问: {raw_path}") from exc
        if not resolved.is_file():
            raise ValueError("请选择一个文件")
        if detect_format(resolved) is None:
            raise ValueError(f"不支持的文件格式: {resolved.suffix or '无扩展名'}")
        data_root = self.paths.root.resolve()
        if data_root == resolved or data_root in resolved.parents:
            raise ValueError("不能导入 EduCore 数据目录内的文件")
        stat = resolved.stat()
        return self.documents.upsert_discovered(
            source_id=None,
            path=str(resolved),
            rel_path=resolved.name,
            format=detect_format(resolved) or "",
            size=stat.st_size,
            mtime=stat.st_mtime,
        )

    def list_sources(self) -> list[dict[str, Any]]:
        return self.sources.list()

    def remove_source(self, source_id: str) -> None:
        source = self.sources.get(source_id)
        if source is None:
            raise ValueError("资料文件夹不存在")
        if source["status"] in {"scanning", "queued"}:
            raise ValueError("该文件夹正在扫描，请完成后再移除")
        self._delete_documents(source_id=source_id)
        self.sources.delete(source_id)

    def remove_document(self, document_id: str) -> None:
        document = self.documents.get(document_id)
        if document is None:
            raise ValueError("文档不存在")
        versions = self.documents.versions(document_id)
        for version in versions:
            self.index.delete_version_fts(version["id"])
            self._delete_version_vectors(version)
        self.documents.delete(document_id)

    def _delete_documents(self, source_id: str) -> None:
        documents = self.documents.list(source_id=source_id, include_missing=True, limit=2000)
        for document in documents:
            for version in self.documents.versions(document["id"]):
                self.index.delete_version_fts(version["id"])
                self._delete_version_vectors(version)
            self.documents.delete(document["id"])

    # -- jobs --------------------------------------------------------------
    def start_scan(
        self,
        *,
        source_id: str | None = None,
        document_id: str | None = None,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        if document_id:
            document = self.documents.get(document_id)
            if document is None:
                raise ValueError("文档不存在")
            job = self.jobs.create(kind="document", document_id=document_id)
            self._spawn(job["id"], force=force)
            return [job]

        if source_id:
            source = self.sources.get(source_id)
            if source is None:
                raise ValueError("资料文件夹不存在")
            job = self.jobs.create(kind="scan", source_id=source_id)
            self._spawn(job["id"], force=force)
            return [job]

        sources = self.sources.list()
        if not sources:
            raise ValueError("还没有添加资料文件夹")
        jobs = [self.jobs.create(kind="scan", source_id=source["id"]) for source in sources]
        for job in jobs:
            self._spawn(job["id"], force=force)
        return jobs

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self.jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.jobs.list(limit)

    def _spawn(self, job_id: str, *, force: bool) -> None:
        task = asyncio.create_task(self._run_job(job_id, force=force))
        self._tasks[job_id] = task

        def _cleanup(_task: asyncio.Task) -> None:
            self._tasks.pop(job_id, None)

        task.add_done_callback(_cleanup)

    async def _run_job(self, job_id: str, *, force: bool) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        try:
            if job["kind"] == "document":
                await self._run_document_job(job, force=force)
            elif job["kind"] == "vectors":
                await self._run_vectors_job(job)
            else:
                await self._run_source_job(job, force=force)
        except asyncio.CancelledError:
            self.jobs.finish(job_id, status="cancelled", message="任务已取消")
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("扫描任务失败 %s", job_id)
            self.jobs.finish(job_id, status="failed", message=f"{type(exc).__name__}: {exc}")

    async def _run_source_job(self, job: dict[str, Any], *, force: bool) -> None:
        source = self.sources.get(job["source_id"])
        if source is None:
            self.jobs.finish(job["id"], status="failed", message="资料文件夹不存在")
            return

        root = Path(source["path"])
        if not root.is_dir():
            self.sources.set_status(source["id"], "failed", "目录不存在或不可访问")
            self.jobs.finish(job["id"], status="failed", message="目录不存在或不可访问")
            return

        self.sources.set_status(source["id"], "scanning")
        self.jobs.start(job["id"], total=0)
        candidates = await asyncio.to_thread(self._discover, root, bool(source["recursive"]))
        self.jobs.progress(job["id"], total=len(candidates))

        seen: list[str] = []
        processed = 0
        failed = 0
        for candidate in candidates:
            try:
                await self._process(source["id"], root, candidate, force=force)
                seen.append(str(candidate.path))
            except Exception as exc:
                failed += 1
                logger.warning("处理文件失败 %s: %s", candidate.path, exc)
            processed += 1
            self.jobs.progress(job["id"], processed=processed, failed=failed)

        # Deleted/renamed files stop being searchable but their rows are kept.
        self.documents.mark_all_missing(source["id"])
        self.documents.restore_present(seen)

        self.sources.set_status(source["id"], "idle")
        self.jobs.finish(
            job["id"],
            status="completed",
            message=f"扫描完成：成功 {processed - failed}，失败 {failed}",
            processed=processed,
            failed=failed,
        )

    async def _run_document_job(self, job: dict[str, Any], *, force: bool) -> None:
        document = self.documents.get(job["document_id"])
        if document is None:
            self.jobs.finish(job["id"], status="failed", message="文档不存在")
            return
        path = Path(document["path"])
        if not path.is_file():
            self.documents.set_status(document["id"], "missing", error="文件不存在")
            self.jobs.finish(job["id"], status="failed", message="文件不存在")
            return

        stat = path.stat()
        candidate = ScanCandidate(
            path=path,
            rel_path=document["rel_path"] or path.name,
            size=stat.st_size,
            mtime=stat.st_mtime,
            format=detect_format(path) or document["format"],
        )
        self.jobs.start(job["id"], total=1)
        try:
            await self._process(document["source_id"], path.parent, candidate, force=force)
            self.jobs.finish(job["id"], status="completed", processed=1, message="索引完成")
        except Exception as exc:
            logger.exception("单文档索引失败 %s", path)
            self.jobs.finish(job["id"], status="failed", failed=1, message=str(exc))

    # -- discovery ---------------------------------------------------------
    def _discover(self, root: Path, recursive: bool) -> list[ScanCandidate]:
        candidates: list[ScanCandidate] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name for name in dirnames if not name.startswith(".") and name not in SKIP_DIRS
            ]
            if not recursive:
                dirnames[:] = []
            for filename in filenames:
                if filename.startswith("."):
                    continue
                path = Path(dirpath) / filename
                fmt = detect_format(path)
                if fmt is None:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_size <= 0 or stat.st_size > MAX_FILE_SIZE:
                    continue
                candidates.append(
                    ScanCandidate(
                        path=path,
                        rel_path=path.relative_to(root).as_posix(),
                        size=stat.st_size,
                        mtime=stat.st_mtime,
                        format=fmt,
                    )
                )
        candidates.sort(key=lambda item: item.rel_path)
        return candidates

    # -- per-file processing ----------------------------------------------
    async def _process(
        self,
        source_id: str | None,
        root: Path,
        candidate: ScanCandidate,
        *,
        force: bool,
    ) -> None:
        # Snapshot the previous state *before* upsert refreshes size/mtime.
        existing = self.documents.get_by_path(str(candidate.path))
        document = self.documents.upsert_discovered(
            source_id=source_id,
            path=str(candidate.path),
            rel_path=candidate.rel_path,
            format=candidate.format,
            size=candidate.size,
            mtime=candidate.mtime,
        )

        ocr_ready = self.ocr.configured()
        same_stat = (
            existing is not None
            and existing["current_version_id"] is not None
            and abs(float(existing["mtime"]) - candidate.mtime) < 1e-6
            and int(existing["size"]) == candidate.size
        )
        previous_status = existing["parse_status"] if existing else ""
        if not force and same_stat and (
            previous_status == "ready" or (previous_status == "needs_ocr" and not ocr_ready)
        ):
            return

        content_hash = await asyncio.to_thread(sha256_file, candidate.path)
        if (
            not force
            and existing is not None
            and existing["content_hash"] == content_hash
            and existing["current_version_id"] is not None
            and (previous_status == "ready" or (previous_status == "needs_ocr" and not ocr_ready))
        ):
            return

        self.documents.set_status(document["id"], "parsing")
        try:
            async with self._parse_semaphore:
                parsed = await asyncio.to_thread(parse_file, candidate.path)
        except ParseError as exc:
            detail = f"{exc.message}" + (f"：{exc.detail}" if exc.detail else "")
            self.documents.set_status(document["id"], "failed", error=detail)
            raise
        except Exception as exc:
            self.documents.set_status(document["id"], "failed", error=f"{type(exc).__name__}: {exc}")
            raise

        if parsed.needs_ocr_pages and ocr_ready:
            await self._apply_ocr(candidate.path, parsed)

        status = "ready" if has_readable_content(parsed) else "needs_ocr"
        if status != "ready":
            status = "needs_ocr" if parsed.needs_ocr_pages else "failed"

        indexer = Indexer(
            cache_dir=self.paths.cache_dir,
            documents=self.documents,
            index=self.index,
            chunking=self.config_provider().chunking,
        )
        previous_version_id = document["current_version_id"]
        result = await asyncio.to_thread(
            indexer.persist,
            document=document,
            parsed=parsed,
            content_hash=content_hash,
            status=status,
        )
        await self._embed_version(
            document["id"],
            result["version"]["id"],
            previous_version_id=previous_version_id,
        )

    async def _apply_ocr(self, path: Path, parsed: ParsedDocument) -> None:
        pages = parsed.needs_ocr_pages
        if not pages:
            return

        if parsed.format == "image":
            try:
                response = await self.ocr.recognize_image_file(path)
            except Exception as exc:
                parsed.warnings.append(f"图片 OCR 失败：{ocr_error_message(exc)}")
                return
            apply_ocr_results(parsed, {1: response})
            return

        semaphore = asyncio.Semaphore(OCR_PAGE_CONCURRENCY)

        async def recognize(page_number: int) -> tuple[int, OCRResponse | Exception]:
            async with semaphore:
                try:
                    return page_number, await self.ocr.recognize_pdf_page(path, page_number)
                except Exception as exc:
                    return page_number, exc

        results = await asyncio.gather(*(recognize(page) for page in pages))
        good: dict[int, OCRResponse] = {}
        for page_number, result in results:
            if isinstance(result, Exception):
                parsed.warnings.append(f"第 {page_number} 页 OCR 失败：{ocr_error_message(result)}")
            else:
                good[page_number] = result
        if good:
            apply_ocr_results(parsed, good)

    # -- embeddings --------------------------------------------------------
    async def _embed_version(
        self,
        document_id: str,
        version_id: str,
        *,
        previous_version_id: str | None = None,
    ) -> None:
        if self.embeddings is None:
            return
        try:
            self.embeddings.endpoint()
        except ModelNotConfigured:
            self.embeddings.mark_no_embedding(version_id)
            return

        chunks = await asyncio.to_thread(self.documents.chunks, version_id)
        if not chunks:
            self.embeddings.mark_no_embedding(version_id)
            return
        try:
            summary = await self.embeddings.index_version(
                version_id=version_id,
                document_id=document_id,
                chunks=chunks,
            )
            logger.info(
                "向量索引完成 doc=%s version=%s profile=%s chunks=%s",
                document_id,
                version_id,
                summary["profile_id"],
                summary["count"],
            )
            if previous_version_id and previous_version_id != version_id:
                await asyncio.to_thread(
                    self._drop_previous_vectors, previous_version_id, summary["profile_id"]
                )
        except ModelError as exc:
            logger.warning("向量索引失败 doc=%s: %s", document_id, exc.message)
            self.embeddings.mark_failed(version_id, exc.message)
        except Exception as exc:  # pragma: no cover - storage/provider failure
            logger.exception("向量索引异常 doc=%s", document_id)
            self.embeddings.mark_failed(version_id, f"{type(exc).__name__}: {exc}")

    def _drop_previous_vectors(self, version_id: str, keep_profile_id: str | None) -> None:
        if self.embeddings is None:
            return
        version = self.documents.get_version(version_id)
        if not version:
            return
        profile_id = version.get("embedding_profile_id")
        if not profile_id or profile_id == keep_profile_id:
            return
        profile = self.embeddings.profiles.get(profile_id)
        if profile:
            try:
                self.embeddings.vectors.delete_version(profile["table_name"], version_id)
            except Exception as exc:  # pragma: no cover
                logger.warning("清理旧向量失败: %s", exc)

    def _delete_version_vectors(self, version: dict[str, Any]) -> None:
        if self.embeddings is None:
            return
        profile_id = version.get("embedding_profile_id")
        if not profile_id:
            return
        profile = self.embeddings.profiles.get(profile_id)
        if profile:
            try:
                self.embeddings.vectors.delete_version(profile["table_name"], version["id"])
            except Exception as exc:  # pragma: no cover
                logger.warning("删除向量失败: %s", exc)

    def _vector_ready(self) -> bool:
        if self.embeddings is None:
            return False
        try:
            self.embeddings.endpoint()
            return True
        except ModelNotConfigured:
            return False

    # -- vector rebuild ----------------------------------------------------
    def start_vector_rebuild(
        self, *, source_id: str | None = None, document_id: str | None = None
    ) -> list[dict[str, Any]]:
        if not self._vector_ready():
            raise ValueError("尚未配置可用的 Embedding 模型")
        if document_id:
            if self.documents.get(document_id) is None:
                raise ValueError("文档不存在")
            job = self.jobs.create(kind="vectors", document_id=document_id)
            self._spawn(job["id"], force=True)
            return [job]
        if source_id and self.sources.get(source_id) is None:
            raise ValueError("资料文件夹不存在")
        job = self.jobs.create(kind="vectors", source_id=source_id)
        self._spawn(job["id"], force=True)
        return [job]

    async def _run_vectors_job(self, job: dict[str, Any]) -> None:
        if self.embeddings is None:
            self.jobs.finish(job["id"], status="failed", message="Embedding 服务未初始化")
            return
        documents = await asyncio.to_thread(
            self.documents.list,
            source_id=job["source_id"],
            limit=2000,
        )
        targets = [document for document in documents if document["current_version_id"]]
        if job["document_id"]:
            targets = [document for document in targets if document["id"] == job["document_id"]]
        self.jobs.start(job["id"], total=len(targets))

        processed = 0
        failed = 0
        for document in targets:
            version_id = document["current_version_id"]
            version = await asyncio.to_thread(self.documents.get_version, version_id)
            chunks = await asyncio.to_thread(self.documents.chunks, version_id) if version else []
            if not chunks:
                processed += 1
                self.jobs.progress(job["id"], processed=processed)
                continue
            try:
                summary = await self.embeddings.index_version(
                    version_id=version_id,
                    document_id=document["id"],
                    chunks=chunks,
                )
                if version and version.get("embedding_profile_id") != summary["profile_id"]:
                    await asyncio.to_thread(
                        self._drop_previous_vectors, version_id, summary["profile_id"]
                    )
            except ModelError as exc:
                failed += 1
                self.embeddings.mark_failed(version_id, exc.message)
            except Exception as exc:  # pragma: no cover
                failed += 1
                logger.exception("重建向量失败 %s", document["path"])
                self.embeddings.mark_failed(version_id, f"{type(exc).__name__}: {exc}")
            processed += 1
            self.jobs.progress(job["id"], processed=processed, failed=failed)

        self.jobs.finish(
            job["id"],
            status="completed",
            message=f"向量重建完成：成功 {processed - failed}，失败 {failed}",
            processed=processed,
            failed=failed,
        )

    # -- targeted re-OCR ---------------------------------------------------
    def _load_parsed(self, version: dict[str, Any] | None) -> ParsedDocument:
        if not version or not version.get("parsed_path"):
            raise ValueError("没有可用的解析快照，请先重新索引该文档")
        path = Path(version["parsed_path"])
        if not path.is_file():
            raise ValueError("解析快照文件缺失，请重新索引该文档")
        return document_from_dict(json.loads(path.read_text(encoding="utf-8")))

    async def reocr_pages(self, document_id: str, pages: list[int]) -> dict[str, Any]:
        document = self.documents.get(document_id)
        if document is None:
            raise ValueError("文档不存在")
        if not document["current_version_id"]:
            raise ValueError("文档尚未完成解析")
        if not self.ocr.configured():
            raise ValueError("尚未配置 OCR 服务")

        version = self.documents.get_version(document["current_version_id"])
        parsed = self._load_parsed(version)
        available = {page.number for page in parsed.pages}
        requested = sorted({int(page) for page in pages if int(page) in available})
        if not requested:
            raise ValueError("指定页码不在文档范围内")

        results: dict[str, Any] = {}
        warnings: list[str] = []
        path = Path(document["path"])
        if parsed.format == "image":
            response = await self.ocr.recognize_image_file(path)
            apply_ocr_results(parsed, {requested[0]: response})
            results[str(requested[0])] = response.text
        else:
            semaphore = asyncio.Semaphore(OCR_PAGE_CONCURRENCY)

            async def recognize(page_number: int) -> tuple[int, OCRResponse | Exception]:
                async with semaphore:
                    try:
                        return page_number, await self.ocr.recognize_pdf_page(path, page_number)
                    except Exception as exc:
                        return page_number, exc

            gathered = await asyncio.gather(*(recognize(page) for page in requested))
            good: dict[int, OCRResponse] = {}
            for page_number, result in gathered:
                if isinstance(result, Exception):
                    warnings.append(f"第 {page_number} 页 OCR 失败：{ocr_error_message(result)}")
                else:
                    good[page_number] = result
                    results[str(page_number)] = result.text
            if good:
                apply_ocr_results(parsed, good)

        status = "ready" if has_readable_content(parsed) else "needs_ocr"
        indexer = Indexer(
            cache_dir=self.paths.cache_dir,
            documents=self.documents,
            index=self.index,
            chunking=self.config_provider().chunking,
        )
        await asyncio.to_thread(
            indexer.persist,
            document=document,
            parsed=parsed,
            content_hash=document["content_hash"] or "",
            status=status,
        )
        return {"pages": requested, "text": results, "warnings": warnings}

    # -- lifecycle ---------------------------------------------------------
    def interrupt_stale(self) -> int:
        return self.jobs.interrupt_stale()

    async def aclose(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    def stats(self) -> dict[str, int]:
        return self.documents.count()