"""ARQ background worker entry point and cron scheduler.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file configures and starts the distributed background worker process:
1. `WorkerSettings`: ARQ configuration registering async task functions, Redis connection,
   retry limits, timeouts, and cron jobs.
2. Cron Jobs:
   - `sync_n8n_executions`: Runs every 5 minutes to reconcile workflows handed to n8n
     that never reported back due to network drops or container restarts.
   - `scan_inactive_learners`: Runs daily at 07:00 UTC to trigger inactivity sweeps
     across active organizations.
3. Lifecyle Hooks:
   - `startup`: Initializes logging and AI providers.
   - `shutdown`: Flushes ARQ connections and disposes SQLAlchemy connection pools.

===============================================================================
HOW IT IS STARTED:
===============================================================================
CLI Command: `arq learnflow.worker.main.WorkerSettings`
Docker Compose: `worker` service runs this entrypoint alongside the FastAPI API container.
"""

from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from ..config import settings
from ..db import dispose_engine, session_scope
from ..logging import configure_logging, get_logger
from ..models import ExecutionStatus, Organization, TriggerType, Workflow, WorkflowExecution
from ..services import jobs as job_service
from .tasks import (
    evaluate_submission,
    generate_assessment,
    generate_course,
    ingest_document,
    regenerate_module,
    run_workflow,
    transform_content,
)

configure_logging(json_output=settings.environment != "development")
log = get_logger("worker")


async def sync_n8n_executions(ctx: dict) -> int:
    """Cron job: Reconciles workflow executions stuck in 'running' state with n8n."""
    from ..automation import n8n_client

    updated = 0
    async with session_scope() as session:
        stale = (
            (
                await session.execute(
                    select(WorkflowExecution).where(
                        WorkflowExecution.status == ExecutionStatus.running,
                        WorkflowExecution.n8n_execution_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for execution in stale:
            data = await n8n_client.get_execution(execution.n8n_execution_id or "")
            if data is None:
                continue
            if data.get("finished"):
                execution.status = (
                    ExecutionStatus.failed
                    if data.get("status") in ("error", "crashed")
                    else ExecutionStatus.succeeded
                )
                execution.output_payload = {**execution.output_payload, "n8n_sync": True}
                updated += 1
    if updated:
        log.info("n8n_sync", updated=updated)
    return updated


async def scan_inactive_learners(ctx: dict) -> int:
    """Cron job: Triggers daily inactivity sweep workflows across active organizations."""
    triggered = 0
    async with session_scope() as session:
        organizations = (
            (await session.execute(select(Organization).where(Organization.is_active.is_(True))))
            .scalars()
            .all()
        )
        for organization in organizations:
            workflows = (
                (
                    await session.execute(
                        select(Workflow).where(
                            Workflow.organization_id == organization.id,
                            Workflow.template == "inactive_reminder",
                            Workflow.is_enabled.is_(True),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for workflow in workflows:
                from ..automation.engine import create_execution

                execution, created = await create_execution(
                    session,
                    workflow,
                    payload={"source": "cron"},
                    trigger_type=TriggerType.schedule,
                    triggered_by="cron:daily",
                    idempotency_key=f"{workflow.key}:{ctx['job_id']}",
                )
                if created:
                    await job_service.enqueue(
                        session,
                        organization.id,
                        "run_workflow",
                        idempotency_key=f"run_workflow:{execution.id}",
                        resource_type="workflow_execution",
                        resource_id=str(execution.id),
                        execution_id=str(execution.id),
                    )
                    triggered += 1
    log.info("inactivity_scan", triggered=triggered)
    return triggered


async def startup(ctx: dict) -> None:
    """Worker startup hook."""
    log.info("worker_startup", provider=settings.llm_provider)


async def shutdown(ctx: dict) -> None:
    """Worker shutdown hook: closes Redis pools and database engines."""
    await job_service.close_arq()
    await dispose_engine()
    log.info("worker_shutdown")


class WorkerSettings:
    """ARQ Worker configuration class."""

    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    # Registered background tasks
    functions = [
        ingest_document,
        generate_course,
        regenerate_module,
        generate_assessment,
        evaluate_submission,
        run_workflow,
        transform_content,
    ]

    # Registered recurring cron schedules
    cron_jobs = [
        cron(
            sync_n8n_executions, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}
        ),  # Every 5 min
        cron(scan_inactive_learners, hour={7}, minute={0}),  # Daily 07:00 UTC
    ]

    on_startup = startup
    on_shutdown = shutdown
    max_tries = 3  # Maximum automatic retries per task
    job_timeout = 900  # 15 minutes timeout for heavy AI generation/ingestion
    keep_result = 3600  # Retain completed job status in Redis for 1 hour
    max_jobs = 5  # Concurrency limit per worker process
