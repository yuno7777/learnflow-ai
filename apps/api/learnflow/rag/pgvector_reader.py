"""Read tenant-scoped matches from the durable pgvector migration destination."""

import json
import math
from typing import Any
from uuid import UUID

import asyncpg

from learnflow.rag.vectorstore import Match


async def query_chunks(
    connection: asyncpg.Connection,
    tenant: UUID,
    embedding: list[float],
    *,
    top_k: int,
    where: dict[str, Any] | None = None,
) -> list[Match]:
    """Rank tenant chunks satisfying every supplied metadata filter."""
    allowed = {
        key: list(value) if isinstance(value, (list, tuple, set)) else [value]
        for key, value in (where or {}).items()
    }
    rows = await connection.fetch(
        """
        SELECT id, body, metadata, embedding <=> $2::vector AS distance
        FROM learnflow_vector.chunks WHERE tenant = $1
          AND NOT EXISTS (
              SELECT 1 FROM jsonb_each($4::jsonb) AS filter(key, choices)
              WHERE NOT EXISTS (
                  SELECT 1 FROM jsonb_array_elements(filter.choices) AS choice(value)
                  WHERE metadata -> filter.key = choice.value
              )
          )
        ORDER BY distance, id LIMIT $3
        """,
        tenant,
        json.dumps(embedding, allow_nan=False),
        top_k,
        json.dumps(allowed, allow_nan=False),
    )
    return [
        Match(
            id=row["id"],
            text=row["body"],
            metadata=(json.loads(row["metadata"]) or {}) if row["metadata"] else {},
            score=(
                max(0.0, min(1.0, 1.0 - row["distance"])) if math.isfinite(row["distance"]) else 0.0
            ),
        )
        for row in rows
    ]
