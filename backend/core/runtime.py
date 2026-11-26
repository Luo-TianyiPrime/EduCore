"""Composition root shared by the API server, tests and workers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from backend.core.agents.runner import AgentRunner
from backend.core.artifacts.service import ArtifactService
from backend.core.config import AppConfig, ConfigStore
from backend.core.indexing.embeddings import EmbeddingService
from backend.core.indexing.service import IndexService
from backend.core.logging import setup_logging
from backend.core.models.gateway import ModelGateway
from backend.core.processing.reading import FullReadEngine
from backend.core.retrieval.pipeline import RetrievalService
from backend.core.retrieval.vector_store import VectorStore
from backend.core.tasks.events import EventBus
from backend.core.tasks.runner import TaskRunner
from backend.platform.paths import DataPaths, resolve_data_dir
from backend.platform.secrets import SecretStore
from backend.storage.db import Database
from backend.storage.documents import DocumentRepo
from backend.storage.embedding_profiles import EmbeddingProfilesRepo
from backend.storage.tasks import TaskRepo

logger = logging.getLogger(__name__)


@dataclass
class AppRuntime:
    paths: DataPaths
    db: Database
    store: ConfigStore
    secrets: SecretStore
    config: AppConfig
    gateway: ModelGateway
    index: IndexService | None = None
    vectors: VectorStore | None = None
    embeddings: EmbeddingService | None = None
    retrieval: RetrievalService | None = None
    tasks_repo: TaskRepo | None = None
    events: EventBus | None = None
    task_runner: TaskRunner | None = None
    artifacts: ArtifactService | None = None

    @classmethod
    def create(cls, data_dir: Path | str | None = None) -> AppRuntime:
        paths = DataPaths(resolve_data_dir(data_dir)).ensure()
        setup_logging(paths.logs_dir)
        logger.info("使用数据目录: %s", paths.root)

        db = Database(paths.database_file)
        db.init()

        store = ConfigStore(paths.config_dir / "settings.json")
        config = store.load()
        secrets = SecretStore(paths.config_dir / "secrets.local.json")
        gateway = ModelGateway(config, secrets, db)

        runtime = cls(
            paths=paths,
            db=db,
            store=store,
            secrets=secrets,
            config=config,
            gateway=gateway,
        )
        documents = DocumentRepo(db)
        vectors = VectorStore(paths.vectors_dir / "lancedb")
        embeddings = EmbeddingService(
            gateway=gateway,
            profiles=EmbeddingProfilesRepo(db),
            vectors=vectors,
            documents=documents,
        )
        runtime.vectors = vectors
        runtime.embeddings = embeddings
        runtime.index = IndexService(
            db=db,
            paths=paths,
            gateway=gateway,
            config_provider=lambda: runtime.config,
            embeddings=embeddings,
        )
        runtime.retrieval = RetrievalService(
            db=db,
            gateway=gateway,
            vectors=vectors,
            config_provider=lambda: runtime.config,
        )
        tasks_repo = TaskRepo(db)
        events = EventBus(tasks_repo)
        reading = FullReadEngine(
            gateway=gateway,
            tasks=tasks_repo,
            paths=paths,
            config_provider=lambda: runtime.config,
        )
        agent_runner = AgentRunner(
            gateway=gateway, db=db, max_steps=runtime.config.max_task_steps
        )
        runtime.tasks_repo = tasks_repo
        runtime.events = events
        runtime.artifacts = ArtifactService(
            db=db,
            paths=paths,
            gateway=gateway,
            retrieval=runtime.retrieval,
            tasks=tasks_repo,
            events=events,
        )
        runtime.task_runner = TaskRunner(
            db=db,
            paths=paths,
            gateway=gateway,
            documents=documents,
            retrieval=runtime.retrieval,
            tasks=tasks_repo,
            events=events,
            config_provider=lambda: runtime.config,
            agent_runner=agent_runner,
            reading=reading,
            artifacts=runtime.artifacts,
        )
        interrupted = runtime.index.interrupt_stale()
        interrupted += runtime.task_runner.interrupt_stale()
        if interrupted:
            logger.warning("有 %d 个任务在上次退出时未完成，已标记为中断", interrupted)
        return runtime

    def save_config(self, config: AppConfig) -> None:
        self.store.save(config)
        self.config = config
        self.gateway.reconfigure(config)

    async def aclose(self) -> None:
        if self.task_runner is not None:
            await self.task_runner.aclose()
        if self.index is not None:
            await self.index.aclose()
        await self.gateway.aclose()
        self.db.close()