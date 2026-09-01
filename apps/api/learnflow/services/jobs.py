"""Background job ledger and ARQ Redis queue integration.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file manages asynchronous background task scheduling:
1. `Job` Database Ledger: Records every async operation in PostgreSQL *before* enqueuing
   to Redis, guaranteeing auditability even if Redis restarts.
2. Idempotency Key Deduping: Enqueuing with an existing `idempotency_key` immediately
   returns the existing Job record without spawning duplicate background work.
3. ARQ Redis Connection Pool (`get_arq`, `close_arq`): Manages async Redis client for ARQ.
4. Dual Queue Engine Support:
   - `redis`: Production mode with ARQ worker processes consuming Redis streams.
   - `database`: Lightweight single-process mode where `standalone.py` polls PostgreSQL directly.
5. Lifecycle Helpers: `start()`, `progress()`, `finish()`, `fail()`.

===============================================================================
WHY DATABASE LEDGER + REDIS QUEUE (Dual-Layer Resilience)?
===============================================================================
Relying solely on Redis for background jobs means in-flight jobs can be lost during Redis
evictions or restarts. By writing a `Job` record to PostgreSQL first, the system maintains
a permanent audit trail, tracks attempt counts, and can reconcile stuck jobs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from arq.connections import ArqRedis, RedisSettings, create_pool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..logging import get_logger
from ..models import Job

log = get_logger(__name__)

_pool: ArqRedis | None = None


async def get_arq() -> ArqRedis:
    """Returns or initializes the singleton ARQ Redis connection pool."""
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def close_arq() -> None:
    """Gracefully closes the ARQ Redis connection pool on application shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
    _pool = None


async def enqueue(
    session: AsyncSession,
    organization_id: uuid.UUID,
    kind: str,
    *,
    idempotency_key: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    max_attempts: int = 3,
    callback_url: str | None = None,
    **task_kwargs: Any,
) -> Job:
    """Creates a Job row in the database ledger and enqueues it to ARQ Redis.

    Args:
        session: Active database session.
        organization_id: Target tenant UUID.
        kind: Task function name (e.g. 'ingest_document', 'run_workflow').
        idempotency_key: Unique string preventing duplicate task dispatch.
        resource_type: Optional entity type (e.g. 'document', 'course').
        resource_id: Optional entity UUID string.
        max_attempts: Max retry attempts before permanent failure.
        callback_url: Optional webhook URL to notify upon completion.
        **task_kwargs: Arguments passed to the worker task function.

    Returns:
        Job: The created or existing Job model instance.
    """
    # 1. Check for existing idempotent job
    existing = await session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing is not None:
        log.info("job_idempotent_hit", kind=kind, job_id=str(existing.id))
        return existing

    # 2. Persist new Job record to PostgreSQL ledger
    job = Job(
        organization_id=organization_id,
        kind=kind,
        status="queued",
        idempotency_key=idempotency_key,
        resource_type=resource_type,
        resource_id=resource_id,
        max_attempts=max_attempts,
        callback_url=callback_url,
        payload=task_kwargs,
    )
    session.add(job)
    await session.flush()

    # In database queue mode, the standalone worker picks up jobs from Postgres
    if settings.job_queue == "database":
        return job

    # 3. Enqueue to Redis stream via ARQ
    try:
        arq = await get_arq()
        enqueued = await arq.enqueue_job(
            kind, str(job.id), str(organization_id), _job_id=f"job:{job.id}", **task_kwargs
        )
        job.arq_job_id = enqueued.job_id if enqueued else None
    except Exception as exc:
        job.status = "failed"
        job.error = f"Could not enqueue: {type(exc).__name__}"
        log.error("job_enqueue_failed", kind=kind, error=str(exc))
    return job


async def start(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """Marks a job as running, increments attempt counter, and stamps start time."""
    job = await session.get(Job, job_id)
    if job is None:
        return None
    job.status = "running"
    job.attempts += 1
    job.started_at = datetime.now(UTC)
    job.progress = 0.0
    await session.flush()
    return job


async def progress(
    session: AsyncSession, job: Job, value: float, message: str | None = None
) -> None:
    """Updates job progress float [0.0, 1.0] and status message."""
    job.progress = max(0.0, min(1.0, value))
    if message:
        job.message = message[:500]
    await session.flush()


async def finish(session: AsyncSession, job: Job, *, result: dict[str, Any] | None = None) -> None:
    """Marks a job successfully finished with result dictionary."""
    job.status = "succeeded"
    job.progress = 1.0
    job.result = result or {}
    job.finished_at = datetime.now(UTC)
    await session.flush()


async def fail(session: AsyncSession, job: Job, error: str) -> None:
    """Marks a job failed or retrying depending on attempt count."""
    job.status = "failed" if job.attempts >= job.max_attempts else "retrying"
    job.error = error[:2000]
    job.finished_at = datetime.now(UTC)
    await session.flush()
