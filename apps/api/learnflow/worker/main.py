"""ARQ worker entrypoint: `arq learnflow.worker.main.WorkerSettings`.

Cron jobs cover the scheduled side of the platform (n8n execution reconciliation and the
daily inactivity sweep) so those keep working even when n8n's own scheduler is not in play.
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
    """Reconcile runs handed to n8n that never called back (n8n crashed, network dropped)."""
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
    """Fire the inactivity workflow once per organization that has one enabled."""
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
    log.info("worker_startup", provider=settings.llm_provider)


async def shutdown(ctx: dict) -> None:
    await job_service.close_arq()
    await dispose_engine()
    log.info("worker_shutdown")


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [
        ingest_document,
        generate_course,
        regenerate_module,
        generate_assessment,
        evaluate_submission,
        run_workflow,
        transform_content,
    ]
    cron_jobs = [
        cron(sync_n8n_executions, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        cron(scan_inactive_learners, hour={7}, minute={0}),
    ]
    on_startup = startup
    on_shutdown = shutdown
    max_tries = 3
    job_timeout = 900
    keep_result = 3600
    max_jobs = 5
