"""Database-backed standalone background worker (Zero-Redis deployment mode).

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file provides a self-contained background task executor for deployments without Redis:
1. PostgreSQL Polling (`_claim`): Polls the `jobs` ledger table for queued or retrying jobs.
2. Safe Concurrency (`FOR UPDATE SKIP LOCKED`): In PostgreSQL, locks claimed rows so multiple
   worker processes can poll the same table without race conditions or duplicate task executions.
3. Task Dispatch (`run_forever`): Dispatches claimed jobs to the exact same task functions
   (`worker/tasks.py`) used by the ARQ Redis worker.
4. Graceful Shutdown (`_install_signal_handlers`): Captures `SIGINT` / `SIGTERM` and finishes
   the current in-flight job before disposing database engines.

===============================================================================
HOW IT IS STARTED:
===============================================================================
CLI Command: `python -m learnflow.worker.standalone`
Usage: Ideal for local development, embedded edge deployments, or minimal cloud footprints.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select

from ..config import settings
from ..db import dispose_engine, session_scope
from ..logging import configure_logging, get_logger
from ..models import Job
from . import tasks

log = get_logger("worker")

POLL_SECONDS = 1.0            # Polling frequency interval
RETRY_BACKOFF_SECONDS = 15    # Minimum wait time before picking up a retrying job

# Dispatch map linking job kind strings to async task functions
HANDLERS: dict[str, Any] = {
    "ingest_document": tasks.ingest_document,
    "generate_course": tasks.generate_course,
    "regenerate_module": tasks.regenerate_module,
    "generate_assessment": tasks.generate_assessment,
    "evaluate_submission": tasks.evaluate_submission,
    "run_workflow": tasks.run_workflow,
    "transform_content": tasks.transform_content,
}

_stopping = asyncio.Event()


async def _claim() -> tuple[str, str, str, dict[str, Any]] | None:
    """Claims the oldest runnable job from the database using row-level locking."""
    async with session_scope() as session:
        retry_cutoff = datetime.now(UTC) - timedelta(seconds=RETRY_BACKOFF_SECONDS)
        statement = (
            select(Job)
            .where(
                or_(
                    Job.status == "queued",
                    (Job.status == "retrying") & (Job.updated_at < retry_cutoff),
                ),
                Job.attempts < Job.max_attempts,
            )
            .order_by(Job.created_at)
            .limit(1)
        )
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            # SKIP LOCKED allows multiple worker processes to claim different jobs simultaneously
            statement = statement.with_for_update(skip_locked=True)
        job = await session.scalar(statement)
        if job is None:
            return None
        
        # Mark running immediately to prevent subsequent polls from picking up this row
        job.status = "running"
        return str(job.id), str(job.organization_id), job.kind, dict(job.payload or {})


async def run_forever() -> None:
    """Main worker event loop: continuously polls and executes claimed jobs."""
    log.info(
        "standalone_worker_started",
        queue=settings.job_queue,
        provider=settings.llm_provider,
        poll_seconds=POLL_SECONDS,
    )
    idle = 0
    while not _stopping.is_set():
        claimed = None
        try:
            claimed = await _claim()
        except Exception:
            log.exception("job_claim_failed")

        if claimed is None:
            idle += 1
            if idle % 60 == 0:
                log.debug("worker_idle", polls=idle)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(_stopping.wait(), timeout=POLL_SECONDS)
            continue

        idle = 0
        job_id, organization_id, kind, payload = claimed
        handler = HANDLERS.get(kind)
        if handler is None:
            log.error("unknown_job_kind", kind=kind, job_id=job_id)
            async with session_scope() as session:
                job = await session.get(Job, uuid.UUID(job_id))
                if job is not None:
                    job.status = "failed"
                    job.error = f"No handler registered for '{kind}'"
            continue

        log.info("job_started", kind=kind, job_id=job_id)
        try:
            await handler({}, job_id, organization_id, **payload)
            log.info("job_succeeded", kind=kind, job_id=job_id)
        except Exception as exc:
            log.error("job_failed", kind=kind, job_id=job_id, error=str(exc)[:300])


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """Installs POSIX and Windows signal handlers for graceful shutdown."""
    def stop(*_: object) -> None:
        log.info("standalone_worker_stopping")
        loop.call_soon_threadsafe(_stopping.set)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            with contextlib.suppress(NotImplementedError, ValueError):
                signal.signal(sig, stop)


async def main() -> None:
    """Standalone worker application bootstrap."""
    configure_logging(json_output=settings.environment != "development")
    _install_signal_handlers(asyncio.get_running_loop())
    try:
        await run_forever()
    finally:
        await dispose_engine()
        log.info("standalone_worker_stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
