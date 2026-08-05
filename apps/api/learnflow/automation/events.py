"""Domain event -> workflow dispatch.

Services emit events (`learner.created`, `ticket.created`, ...); this finds the enabled
workflows subscribed to that event, creates one execution each and queues the actual run on
the worker. Nothing blocking happens in the request path.
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
    event = AutomationEvent(organization_id=organization_id, name=name, payload=payload)
    session.add(event)
    await session.flush()

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

    event.dispatched = True
    event.execution_ids = [str(i) for i in execution_ids]
    await session.flush()
    # `event` is structlog's reserved key for the message itself — use event_name here.
    log.info("event_emitted", event_name=name, workflows=len(workflows))
    return execution_ids
