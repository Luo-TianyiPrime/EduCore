"""Embedding of indexed chunks with profile-aware vector bookkeeping."""

from __future__ import annotations

import logging
from typing import Any

from backend.core.config import EndpointConfig
from backend.core.models.gateway import ModelGateway
from backend.core.models.types import ModelNotConfigured
from backend.core.retrieval.vector_store import VectorStore
from backend.storage.documents import DocumentRepo
from backend.storage.embedding_profiles import EmbeddingProfilesRepo

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        profiles: EmbeddingProfilesRepo,
        vectors: VectorStore,
        documents: DocumentRepo,
    ) -> None:
        self.gateway = gateway
        self.profiles = profiles
        self.vectors = vectors
        self.documents = documents

    def endpoint(self) -> EndpointConfig:
        return self.gateway.resolve("embedding")

    async def index_version(
        self,
        *,
        version_id: str,
        document_id: str,
        chunks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Embed every chunk of a version and store it under its profile."""
        endpoint = self.endpoint()
        if not chunks:
            return {"profile_id": None, "count": 0}

        batch_size = max(1, endpoint.batch_size)
        first_texts = [self._chunk_text(chunk) for chunk in chunks[:batch_size]]
        first = await self.gateway.embed(first_texts, endpoint_id=endpoint.id, role="document")
        dimensions = endpoint.dimensions or first.dimensions
        if first.dimensions and dimensions != first.dimensions:
            raise ValueError(
                f"Embedding 维度与配置不符：配置 {dimensions}，实际 {first.dimensions}"
            )

        profile = self.profiles.ensure(endpoint, dimensions)
        table_name = profile["table_name"]
        vector_rows: list[dict[str, Any]] = []
        vectors = first.vectors
        for offset in range(0, len(chunks), batch_size):
            if offset > 0:
                texts = [self._chunk_text(chunk) for chunk in chunks[offset : offset + batch_size]]
                response = await self.gateway.embed(texts, endpoint_id=endpoint.id, role="document")
                vectors = response.vectors
            batch_chunks = chunks[offset : offset + batch_size]
            for chunk, vector in zip(batch_chunks, vectors, strict=False):
                vector_rows.append(
                    {
                        "chunk_id": chunk["id"],
                        "version_id": version_id,
                        "document_id": document_id,
                        "vector": vector,
                    }
                )

        self.vectors.add(table_name, dimensions, vector_rows)
        self.vectors.delete_document_except_version(table_name, document_id, version_id)
        self.profiles.touch(profile["id"])
        self.documents.set_vector_status(
            version_id,
            profile_id=profile["id"],
            status="ready",
            count=len(vector_rows),
            error=None,
        )
        return {"profile_id": profile["id"], "count": len(vector_rows), "dimensions": dimensions}

    def mark_no_embedding(self, version_id: str) -> None:
        self.documents.set_vector_status(
            version_id, profile_id=None, status="none", count=0, error=None
        )

    def mark_failed(self, version_id: str, error: str) -> None:
        self.documents.set_vector_status(
            version_id, profile_id=None, status="failed", count=0, error=error[:500]
        )

    @staticmethod
    def _chunk_text(chunk: dict[str, Any]) -> str:
        heading = (chunk.get("heading") or "").strip()
        text = (chunk.get("text") or "").strip()
        return f"{heading}\n{text}".strip() if heading else text


def is_configured(gateway: ModelGateway) -> bool:
    try:
        gateway.resolve("embedding")
        return True
    except ModelNotConfigured:
        return False