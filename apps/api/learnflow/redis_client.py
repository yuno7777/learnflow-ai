"""Shared async Redis connection pool (rate limiting, SSE pub/sub, job progress)."""

from __future__ import annotations

import redis.asyncio as aioredis

from .config import settings

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


EXECUTION_CHANNEL = "learnflow:execution:{execution_id}"
JOB_CHANNEL = "learnflow:job:{job_id}"
