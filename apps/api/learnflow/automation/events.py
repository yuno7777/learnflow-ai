"""Domain event dispatcher and workflow execution trigger.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file implements the internal **Event-Driven Architecture** of LearnFlow:
1. `emit()`: Emits a domain event (e.g. `learner.created`, `ticket.created`,
   `evaluation.needs_review`, `schedule.daily`).
2. Persists the `AutomationEvent` to PostgreSQL.
3. Queries for all active `Workflow` rows subscribed to that event in the tenant.
4. Creates an idempotent `WorkflowExecution` row for each matching workflow.
5. Enqueues a background job (`run_workflow`) via `services.jobs.enqueue()`.

===============================================================================
WHY ASYNCHRONOUS EVENT DISPATCH?
===============================================================================
When a user registers or submits an assessment, we must NOT block the HTTP request
waiting for external email sends, n8n webhook HTTP handshakes, or LLM summarizations.
`events.emit()` creates database records in milliseconds and delegates the execution
to background workers, guaranteeing sub-50ms API response times.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logging import get_logger
from ..models import AutomationEvent, TriggerType, Workflow
from ..services import jobs
from .engine import create_execution

log = get_logger(__name__)


async def emit(
    session: AsyncSession,
    organization_id: uuid.UUID,
    name: str,
    payload: dict[str, Any],
    *,
    learner_id: uuid.UUID | None = None,
    course_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> list[uuid.UUID]:
    """Emits a domain event and triggers all matching subscriber workflows.

    Execution Flow:
    1. Saves `AutomationEvent` to the database.
    2. Selects all `Workflow` records matching `organization_id`, `trigger_event == name`,
       and `is_enabled == True`.
    3. Calls `create_execution()` to instantiate the workflow run.
    4. Enqueues `run_workflow` task into ARQ worker queue.
    5. Marks the event as dispatched.

    Returns:
        list[uuid.UUID]: List of created `WorkflowExecution` IDs.
    """
    # 1. Record event row in database
    event = AutomationEvent(organization_id=organization_id, name=name, payload=payload)
    session.add(event)
    await session.flush()

    # 2. Find enabled subscriber workflows
    workflows = list(
        (
            await session.execute(
                select(Workflow).where(
                    Workflow.organization_id == organization_id,
                    Workflow.trigger_event == name,
                    Workflow.is_enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )

    # 3. Create executions and enqueue worker jobs
    execution_ids: list[uuid.UUID] = []
    for workflow in workflows:
        execution, created = await create_execution(
            session,
            workflow,
            payload=payload,
            trigger_type=TriggerType.event,
            triggered_by=f"event:{name}",
            idempotency_key=f"{workflow.key}:{idempotency_key or event.id}",
            learner_id=learner_id,
            course_id=course_id,
        )
        execution_ids.append(execution.id)
        if created:
            # Enqueue async job to be claimed by ARQ worker
            await jobs.enqueue(
                session,
                organization_id,
                "run_workflow",
                idempotency_key=f"run_workflow:{execution.id}",
                resource_type="workflow_execution",
                resource_id=str(execution.id),
                max_attempts=workflow.max_retries + 1,
                execution_id=str(execution.id),
            )

    # 4. Mark event dispatched and return execution IDs
    event.dispatched = True
    event.execution_ids = [str(i) for i in execution_ids]
    await session.flush()
    log.info("event_emitted", event_name=name, workflows=len(workflows))
    return execution_ids
