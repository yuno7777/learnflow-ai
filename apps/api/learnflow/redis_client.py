"""Shared async Redis connection pool (rate limiting, SSE pub/sub, job progress).

Redis is optional. Setting `REDIS_URL=""` selects single-node mode: rate limiting and live
streaming are skipped and the job queue falls back to the database. Callers must check
`redis_enabled()` first — `get_redis()` raises immediately rather than spending a connection
timeout on every request.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from .config import settings

_client: aioredis.Redis | None = None


def redis_enabled() -> bool:
    return bool(settings.redis_url)


def get_redis() -> aioredis.Redis:
    if not redis_enabled():
        raise RuntimeError("Redis is disabled (REDIS_URL is empty)")
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
