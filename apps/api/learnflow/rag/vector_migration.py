"""Copy a quiescent Chroma collection into a connection-local pgvector staging table."""

import asyncio
import json
from uuid import UUID

import asyncpg


async def copy_collection(
    collection, connection: asyncpg.Connection, tenant: UUID, *, batch_size=256
):
    """Copy a snapshot in bounded pages. The caller owns the staging table and connection."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    total = await asyncio.to_thread(collection.count)
    copied = 0
    async with connection.transaction():
        while copied < total:
            page = await asyncio.to_thread(
                collection.get,
                limit=batch_size,
                offset=copied,
                include=["documents", "metadatas", "embeddings"],
            )
            if not page["ids"]:
                raise RuntimeError("Source collection changed during migration")
            rows = [
                (
                    tenant,
                    record_id,
                    body,
                    json.dumps(metadata),
                    json.dumps(list(map(float, vector))),
                )
                for record_id, body, metadata, vector in zip(
                    page["ids"],
                    page["documents"],
                    page["metadatas"],
                    page["embeddings"],
                    strict=True,
                )
            ]
            await connection.executemany(
                """
                INSERT INTO pg_temp.chunks (tenant, id, body, metadata, embedding)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (tenant, id) DO UPDATE SET
                    body = excluded.body, metadata = excluded.metadata,
                    embedding = excluded.embedding
                """,
                rows,
            )
            copied += len(rows)
        if copied != total or await asyncio.to_thread(collection.count) != total:
            raise RuntimeError("Source collection changed during migration")
    return copied
