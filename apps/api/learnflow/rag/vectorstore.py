"""Vector store abstraction and ChromaDB multi-tenant client.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file manages vector storage and similarity search for RAG retrieval:
1. `VectorRecord` & `Match`: Standard vector payload and search match dataclasses.
2. `VectorStore`: Abstract protocol defining `upsert`, `query`, `delete`, and `count`.
3. `ChromaVectorStore`: Production ChromaDB client supporting HTTP and local persistent modes.
4. `InMemoryVectorStore`: Pure Python in-memory vector store with direct cosine math for tests.
5. `collection_name(organization_id)`: Formats tenant collection names (`org_<uuid.hex>`).

===============================================================================
CRITICAL MULTI-TENANT DESIGN DECISION (Collection Isolation):
===============================================================================
Tenant isolation is enforced at the **collection level**, NOT via metadata filtering.
- Why? In a shared vector collection, if a developer forgets a `where={"org_id": ...}`
  filter in a query, the search accidentally leaks another tenant's proprietary documents.
- In collection-per-tenant isolation, querying with the wrong organization ID opens an
  isolated collection that simply has zero matching documents—failing closed.
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
    """A document chunk embedding prepared for insertion into the vector database."""

    id: str  # Formatted as "{document_id}:{position}"
    embedding: list[float]  # Dense vector (e.g. 1536 floats)
    text: str  # Raw text content of the chunk
    metadata: dict[str, Any] = field(default_factory=dict)  # Document ID, page, section


@dataclass(slots=True)
class Match:
    """A similarity search result returned by the vector store."""

    id: str
    text: str
    score: float  # Normalized cosine similarity score in [0.0, 1.0] (1.0 = identical)
    metadata: dict[str, Any]


class VectorStore(Protocol):
    """Abstract protocol for vector datastores (ChromaDB, Pinecone, Qdrant, In-Memory)."""

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
    """Generates the isolated ChromaDB collection identifier for a tenant (e.g. `org_9a4f21...`)."""
    return f"org_{organization_id.hex}"


def _chroma_where(where: dict[str, Any] | None) -> dict[str, Any] | None:
    """Translates standard python dict filters into ChromaDB's `$and` / `$in` filter syntax."""
    if not where:
        return None
    clauses = [
        {key: {"$in": list(value)}} if isinstance(value, (list, tuple, set)) else {key: value}
        for key, value in where.items()
    ]
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


class ChromaVectorStore:
    """ChromaDB implementation of `VectorStore`."""

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        """Initializes ChromaDB HTTP client (in prod) or PersistentClient (in local/CLI)."""
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
        """Retrieves or creates the organization's dedicated HNSW cosine collection."""
        return self._get_client().get_or_create_collection(
            name=collection_name(organization_id), metadata={"hnsw:space": "cosine"}
        )

    async def upsert(self, organization_id: uuid.UUID, records: list[VectorRecord]) -> None:
        """Upserts embedding vectors and metadata in ChromaDB (runs in background thread)."""
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
        """Queries ChromaDB collection for nearest neighbors and normalizes distance to
        similarity.
        """

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
                # Chroma returns cosine distance (0 = identical, 2 = opposite).
                # Convert to similarity score in [0.0, 1.0]: score = 1.0 - distance
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
        """Deletes vectors from an organization's collection."""
        if not ids and not where:
            raise ValueError("Refusing to delete the entire collection without a filter")

        def _run() -> None:
            self._collection(organization_id).delete(ids=ids, where=_chroma_where(where))

        await asyncio.to_thread(_run)

    async def count(self, organization_id: uuid.UUID) -> int:
        """Returns total vector count in the tenant's collection."""
        return await asyncio.to_thread(lambda: self._collection(organization_id).count())


class InMemoryVectorStore:
    """In-memory mock vector store used during automated unit tests."""

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
    """Sets a global override vector store instance (used during testing)."""
    global _override
    _override = store
    _default_store.cache_clear()


@lru_cache(maxsize=1)
def _default_store() -> VectorStore:
    return ChromaVectorStore()


def get_vector_store() -> VectorStore:
    """Returns the active vector store singleton."""
    return _override or _default_store()
