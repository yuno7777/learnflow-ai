"""Vector store abstraction with a ChromaDB implementation.

Tenant isolation is by **collection**, not by metadata filter: `org_<uuid.hex>`. A forgotten
`where` clause on a shared collection leaks other tenants' documents; a wrong collection name
simply returns nothing. Pinecone/Weaviate adapters implement the same three methods
(namespace / class per org respectively).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

from ..config import settings
from ..logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class VectorRecord:
    id: str
    embedding: list[float]
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Match:
    id: str
    text: str
    score: float  # cosine similarity in [0, 1]; higher is better
    metadata: dict[str, Any]


class VectorStore(Protocol):
    async def upsert(self, organization_id: uuid.UUID, records: list[VectorRecord]) -> None: ...

    async def query(
        self,
        organization_id: uuid.UUID,
        embedding: list[float],
        *,
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[Match]: ...

    async def delete(
        self,
        organization_id: uuid.UUID,
        *,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None: ...

    async def count(self, organization_id: uuid.UUID) -> int: ...


def collection_name(organization_id: uuid.UUID) -> str:
    return f"org_{organization_id.hex}"


def _chroma_where(where: dict[str, Any] | None) -> dict[str, Any] | None:
    """Chroma needs an explicit $and for multi-key filters and $in for lists."""
    if not where:
        return None
    clauses = [
        {key: {"$in": list(value)}} if isinstance(value, (list, tuple, set)) else {key: value}
        for key, value in where.items()
    ]
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


class ChromaVectorStore:
    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is None:
            import chromadb

            if settings.chroma_persist_dir:
                self._client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
            else:
                self._client = chromadb.HttpClient(
                    host=settings.chroma_host, port=settings.chroma_port
                )
        return self._client

    def _collection(self, organization_id: uuid.UUID):
        return self._get_client().get_or_create_collection(
            name=collection_name(organization_id), metadata={"hnsw:space": "cosine"}
        )

    async def upsert(self, organization_id: uuid.UUID, records: list[VectorRecord]) -> None:
        if not records:
            return

        def _run() -> None:
            self._collection(organization_id).upsert(
                ids=[r.id for r in records],
                embeddings=[r.embedding for r in records],
                documents=[r.text for r in records],
                metadatas=[r.metadata for r in records],
            )

        await asyncio.to_thread(_run)

    async def query(
        self,
        organization_id: uuid.UUID,
        embedding: list[float],
        *,
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[Match]:
        def _run() -> Any:
            return self._collection(organization_id).query(
                query_embeddings=[embedding],
                n_results=top_k,
                where=_chroma_where(where),
                include=["documents", "metadatas", "distances"],
            )

        result = await asyncio.to_thread(_run)
        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        return [
            Match(
                id=i,
                text=d or "",
                # Chroma returns cosine *distance*; clamp because float error can go slightly <0.
                score=max(0.0, min(1.0, 1.0 - float(dist))),
                metadata=meta or {},
            )
            for i, d, meta, dist in zip(ids, docs, metas, dists, strict=False)
        ]

    async def delete(
        self,
        organization_id: uuid.UUID,
        *,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None:
        if not ids and not where:
            raise ValueError("Refusing to delete the entire collection without a filter")

        def _run() -> None:
            self._collection(organization_id).delete(ids=ids, where=_chroma_where(where))

        await asyncio.to_thread(_run)

    async def count(self, organization_id: uuid.UUID) -> int:
        return await asyncio.to_thread(lambda: self._collection(organization_id).count())


class InMemoryVectorStore:
    """Used by unit tests. Same semantics, cosine similarity computed directly."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, VectorRecord]] = {}

    def _bucket(self, organization_id: uuid.UUID) -> dict[str, VectorRecord]:
        return self._data.setdefault(collection_name(organization_id), {})

    async def upsert(self, organization_id: uuid.UUID, records: list[VectorRecord]) -> None:
        bucket = self._bucket(organization_id)
        for record in records:
            bucket[record.id] = record

    async def query(
        self,
        organization_id: uuid.UUID,
        embedding: list[float],
        *,
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[Match]:
        def matches(meta: dict[str, Any]) -> bool:
            for key, value in (where or {}).items():
                if isinstance(value, (list, tuple, set)):
                    if meta.get(key) not in value:
                        return False
                elif meta.get(key) != value:
                    return False
            return True

        def cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b, strict=False))
            na = sum(x * x for x in a) ** 0.5 or 1.0
            nb = sum(y * y for y in b) ** 0.5 or 1.0
            return dot / (na * nb)

        scored = [
            Match(id=r.id, text=r.text, score=cosine(embedding, r.embedding), metadata=r.metadata)
            for r in self._bucket(organization_id).values()
            if matches(r.metadata)
        ]
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:top_k]

    async def delete(
        self,
        organization_id: uuid.UUID,
        *,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None:
        bucket = self._bucket(organization_id)
        if ids:
            for i in ids:
                bucket.pop(i, None)
        if where:

            def matches(meta: dict[str, Any]) -> bool:
                for key, value in where.items():
                    if isinstance(value, (list, tuple, set)):
                        if meta.get(key) not in value:
                            return False
                    elif meta.get(key) != value:
                        return False
                return True

            for rid in [r.id for r in list(bucket.values()) if matches(r.metadata)]:
                bucket.pop(rid, None)

    async def count(self, organization_id: uuid.UUID) -> int:
        return len(self._bucket(organization_id))


_override: VectorStore | None = None


def set_vector_store(store: VectorStore | None) -> None:
    """Tests swap in the in-memory store; production never calls this."""
    global _override
    _override = store
    _default_store.cache_clear()


@lru_cache(maxsize=1)
def _default_store() -> VectorStore:
    return ChromaVectorStore()


def get_vector_store() -> VectorStore:
    return _override or _default_store()
