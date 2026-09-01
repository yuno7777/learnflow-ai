"""Shared asynchronous Redis connection pool and Pub/Sub channel constants.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file manages the application's connection to Redis. It provides:
1. `redis_enabled()`: Checks if `REDIS_URL` is configured.
2. `get_redis()`: Returns a shared async Redis client singleton (`redis.asyncio`).
3. `close_redis()`: Closes connection pools gracefully on application shutdown.
4. Channel name format templates for Pub/Sub live streaming:
   - `EXECUTION_CHANNEL`: For streaming real-time workflow step executions to SSE.
   - `JOB_CHANNEL`: For streaming 10-stage document ingestion progress to SSE.

===============================================================================
WHY THIS FILE EXISTS & HOW IT CONNECTS:
===============================================================================
- `middleware.py`: Calls `get_redis()` for atomic `INCR` rate-limiting counters.
- `rag/pipeline.py` & `worker/tasks.py`: Publishes stage progress events to Redis channels.
- `routers/knowledge.py` & `routers/workflows.py`: Subscribes to Redis Pub/Sub channels
  and converts Redis messages into Server-Sent Events (SSE) for the frontend dashboard.
- `main.py`: Calls `close_redis()` inside the FastAPI lifespan context manager.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from .config import settings

# Global Redis client singleton
_client: aioredis.Redis | None = None


def redis_enabled() -> bool:
    """Returns True if a Redis URL is configured in settings.

    Allows the system to gracefully disable rate limiting and SSE streaming when running
    in minimal/single-node local environments.
    """
    return bool(settings.redis_url)


def get_redis() -> aioredis.Redis:
    """Returns the shared asynchronous Redis client instance.

    Throws RuntimeError immediately if Redis is disabled, preventing connection
    timeouts on every request.
    """
    if not redis_enabled():
        raise RuntimeError("Redis is disabled (REDIS_URL is empty)")
    global _client
    if _client is None:
        # `decode_responses=True` ensures Redis strings are automatically decoded from bytes to str
        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def close_redis() -> None:
    """Closes all active Redis connections in the pool gracefully.

    Called during application shutdown in `learnflow/main.py`.
    """
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


# Pub/Sub Redis Channel Patterns:
# Formatted with UUIDs to isolate live event streams by entity ID.
EXECUTION_CHANNEL = "learnflow:execution:{execution_id}"
JOB_CHANNEL = "learnflow:job:{job_id}"
