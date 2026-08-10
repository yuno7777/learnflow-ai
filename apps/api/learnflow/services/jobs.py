"""Background job ledger + ARQ enqueue.

Every async unit of work gets a `Job` row *before* it is enqueued, keyed by an idempotency
key. Enqueuing the same key twice returns the existing job instead of duplicating work — the
same guarantee the webhook path needs when n8n retries.
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
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def close_arq() -> None:
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
    existing = await session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing is not None:
        log.info("job_idempotent_hit", kind=kind, job_id=str(existing.id))
        return existing

    job = Job(
        organization_id=organization_id,
        kind=kind,
        status="queued",
        idempotency_key=idempotency_key,
        resource_type=resource_type,
        resource_id=resource_id,
        max_attempts=max_attempts,
        callback_url=callback_url,
        # Persisted so the database-backed worker can dispatch without Redis, and so the
        # job ledger shows what a task was actually asked to do.
        payload=task_kwargs,
    )
    session.add(job)
    await session.flush()

    if settings.job_queue == "database":
        # Single-node mode: the standalone worker claims queued rows by polling.
        return job

    try:
        arq = await get_arq()
        enqueued = await arq.enqueue_job(
            kind, str(job.id), str(organization_id), _job_id=f"job:{job.id}", **task_kwargs
        )
        job.arq_job_id = enqueued.job_id if enqueued else None
    except Exception as exc:
        # Surface the failure instead of leaving a job "queued" forever with no worker holding it.
        job.status = "failed"
        job.error = f"Could not enqueue: {type(exc).__name__}"
        log.error("job_enqueue_failed", kind=kind, error=str(exc))
    return job


async def start(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
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
    job.progress = max(0.0, min(1.0, value))
    if message:
        job.message = message[:500]
    await session.flush()


async def finish(session: AsyncSession, job: Job, *, result: dict[str, Any] | None = None) -> None:
    job.status = "succeeded"
    job.progress = 1.0
    job.result = result or {}
    job.finished_at = datetime.now(UTC)
    await session.flush()


async def fail(session: AsyncSession, job: Job, error: str) -> None:
    job.status = "failed" if job.attempts >= job.max_attempts else "retrying"
    job.error = error[:2000]
    job.finished_at = datetime.now(UTC)
    await session.flush()
