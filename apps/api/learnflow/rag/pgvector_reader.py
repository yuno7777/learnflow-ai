"""Read tenant-scoped matches from the durable pgvector migration destination."""

import json
import math
from uuid import UUID

import asyncpg

from learnflow.rag.vectorstore import Match


async def query_chunks(
    connection: asyncpg.Connection, tenant: UUID, embedding: list[float], *, top_k: int
) -> list[Match]:
    """Return unfiltered nearest neighbors as application matches."""
    rows = await connection.fetch(
        """
        SELECT id, body, metadata, embedding <=> $2::vector AS distance
        FROM learnflow_vector.chunks WHERE tenant = $1
        ORDER BY distance, id LIMIT $3
        """,
        tenant,
        json.dumps(embedding, allow_nan=False),
        top_k,
    )
    return [
        Match(
            id=row["id"],
            text=row["body"],
            metadata=(json.loads(row["metadata"]) or {}) if row["metadata"] else {},
            score=(
                max(0.0, min(1.0, 1.0 - row["distance"]))
                if math.isfinite(row["distance"])
                else 0.0
            ),
        )
        for row in rows
    ]
