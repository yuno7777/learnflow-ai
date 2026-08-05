"""Workflow definitions, manual triggers, execution history and live run streaming."""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sse_starlette.sse import EventSourceResponse

from ..automation.engine import create_execution
from ..automation.runners import TEMPLATES
from ..deps import PrincipalDep, RepoDep, SessionDep, require
from ..errors import Conflict
from ..models import ExecutionStatus, TriggerType, Workflow, WorkflowExecution, WorkflowStep
from ..redis_client import EXECUTION_CHANNEL, get_redis
from ..schemas.automation import (
    ExecutionDetail,
    ExecutionOut,
    TriggerRequest,
    WorkflowCreate,
    WorkflowOut,
    WorkflowStepOut,
    WorkflowUpdate,
)
from ..services import audit, jobs

router = APIRouter(prefix="/workflows", tags=["workflows"])
executions_router = APIRouter(prefix="/executions", tags=["workflows"])

TERMINAL = {ExecutionStatus.succeeded, ExecutionStatus.failed, ExecutionStatus.cancelled}


@router.get("/templates", summary="Available workflow templates")
async def templates() -> dict[str, dict]:
    return TEMPLATES


@router.get("", response_model=list[WorkflowOut], summary="List workflows")
async def list_workflows(repo: RepoDep) -> list[Workflow]:
    items, _ = await repo.list(Workflow, sort="created_at", limit=100)
    return items


@router.post(
    "",
    response_model=WorkflowOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require("workflow:write"))],
    summary="Create a workflow from a template",
)
async def create_workflow(
    body: WorkflowCreate,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> Workflow:
    if body.template not in TEMPLATES:
        raise Conflict("Unknown template", {"available": sorted(TEMPLATES)})
    existing = await session.scalar(repo.select(Workflow).where(Workflow.key == body.key))
    if existing is not None:
        raise Conflict("A workflow with that key already exists")

    defaults = TEMPLATES[body.template]
    payload = body.model_dump()
    payload["trigger_event"] = body.trigger_event or defaults.get("trigger_event")
    payload["config"] = {**defaults.get("config", {}), **body.config}
    workflow = repo.add(Workflow(**payload))
    await repo.flush()
    await audit.record(
        session, principal, "workflow.create", "workflow", str(workflow.id), request=request
    )
    return workflow


@router.get("/{workflow_id}", response_model=WorkflowOut, summary="Workflow detail")
async def get_workflow(workflow_id: uuid.UUID, repo: RepoDep) -> Workflow:
    return await repo.get(Workflow, workflow_id)


@router.patch(
    "/{workflow_id}",
    response_model=WorkflowOut,
    dependencies=[Depends(require("workflow:write"))],
    summary="Update a workflow",
)
async def update_workflow(workflow_id: uuid.UUID, body: WorkflowUpdate, repo: RepoDep) -> Workflow:
    workflow = await repo.get(Workflow, workflow_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(workflow, field, value)
    await repo.flush()
    return workflow


@router.post(
    "/{workflow_id}/trigger",
    response_model=ExecutionOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require("workflow:run"))],
    summary="Trigger a workflow run",
)
async def trigger(
    workflow_id: uuid.UUID,
    body: TriggerRequest,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> WorkflowExecution:
    workflow = await repo.get(Workflow, workflow_id)
    if not workflow.is_enabled:
        raise Conflict("Workflow is disabled")

    execution, created = await create_execution(
        session,
        workflow,
        payload=body.payload,
        trigger_type=TriggerType.manual,
        triggered_by=principal.actor_label,
        idempotency_key=body.idempotency_key,
        learner_id=body.learner_id,
        course_id=body.course_id,
    )
    if created:
        await jobs.enqueue(
            session,
            repo.organization_id,
            "run_workflow",
            idempotency_key=f"run_workflow:{execution.id}",
            resource_type="workflow_execution",
            resource_id=str(execution.id),
            max_attempts=workflow.max_retries + 1,
            execution_id=str(execution.id),
        )
        await audit.record(
            session,
            principal,
            "workflow.trigger",
            "workflow_execution",
            str(execution.id),
            request=request,
            details={"workflow": workflow.key},
        )
    return execution


# ------------------------------------------------------------------ executions
@executions_router.get("", response_model=dict, summary="List workflow executions")
async def list_executions(
    repo: RepoDep,
    session: SessionDep,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    workflow_id: uuid.UUID | None = Query(None),
    execution_status: ExecutionStatus | None = Query(None, alias="status"),
) -> dict:
    where = []
    if workflow_id:
        where.append(WorkflowExecution.workflow_id == workflow_id)
    if execution_status:
        where.append(WorkflowExecution.status == execution_status)
    items, total = await repo.list(
        WorkflowExecution, where=where, sort="-created_at", limit=limit, offset=offset
    )
    rows = (
        await session.execute(repo.select(Workflow).with_only_columns(Workflow.id, Workflow.name))
    ).all()
    names: dict[uuid.UUID, str] = {row[0]: row[1] for row in rows}
    return {
        "items": [
            {
                **ExecutionOut.model_validate(e).model_dump(),
                "workflow_name": names.get(e.workflow_id),
            }
            for e in items
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@executions_router.get(
    "/{execution_id}", response_model=ExecutionDetail, summary="Full execution record"
)
async def get_execution(
    execution_id: uuid.UUID, repo: RepoDep, session: SessionDep
) -> ExecutionDetail:
    execution = await repo.get(WorkflowExecution, execution_id)
    workflow = await repo.get(Workflow, execution.workflow_id)
    steps = (
        (
            await session.execute(
                repo.select(WorkflowStep)
                .where(WorkflowStep.execution_id == execution.id)
                .order_by(WorkflowStep.position)
            )
        )
        .scalars()
        .all()
    )
    detail = ExecutionDetail.model_validate(execution)
    detail.workflow_name = workflow.name
    detail.workflow_key = workflow.key
    detail.steps = [WorkflowStepOut.model_validate(s) for s in steps]
    return detail


@executions_router.post(
    "/{execution_id}/retry",
    response_model=ExecutionOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require("workflow:run"))],
    summary="Manually retry a failed execution",
)
async def retry_execution(
    execution_id: uuid.UUID,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> WorkflowExecution:
    execution = await repo.get(WorkflowExecution, execution_id)
    if execution.status not in (ExecutionStatus.failed, ExecutionStatus.cancelled):
        raise Conflict("Only failed or cancelled executions can be retried")
    execution.retry_count += 1
    execution.status = ExecutionStatus.queued
    execution.error = None
    execution.finished_at = None
    await repo.flush()
    await jobs.enqueue(
        session,
        repo.organization_id,
        "run_workflow",
        idempotency_key=f"run_workflow:{execution.id}:retry{execution.retry_count}",
        resource_type="workflow_execution",
        resource_id=str(execution.id),
        execution_id=str(execution.id),
    )
    await audit.record(
        session,
        principal,
        "workflow.retry",
        "workflow_execution",
        str(execution.id),
        request=request,
        details={"retry_count": execution.retry_count},
    )
    return execution


@executions_router.get(
    "/{execution_id}/stream",
    summary="Server-sent events: live execution updates",
    response_class=EventSourceResponse,
)
async def stream_execution(execution_id: uuid.UUID, repo: RepoDep, session: SessionDep):
    execution = await repo.get(WorkflowExecution, execution_id)

    async def publisher():
        steps = (
            (
                await session.execute(
                    repo.select(WorkflowStep)
                    .where(WorkflowStep.execution_id == execution.id)
                    .order_by(WorkflowStep.position)
                )
            )
            .scalars()
            .all()
        )
        yield {
            "event": "snapshot",
            "data": json.dumps(
                {
                    "status": execution.status.value,
                    "steps": [{"name": s.name, "status": s.status, "kind": s.kind} for s in steps],
                },
                default=str,
            ),
        }
        if execution.status in TERMINAL:
            yield {"event": "done", "data": json.dumps({"status": execution.status.value})}
            return

        pubsub = get_redis().pubsub()
        await pubsub.subscribe(EXECUTION_CHANNEL.format(execution_id=execution_id))
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if message is None:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {"event": "update", "data": message["data"]}
                try:
                    parsed = json.loads(message["data"])
                except (TypeError, ValueError):
                    continue
                if parsed.get("type") == "status" and parsed.get("status") in {
                    s.value for s in TERMINAL
                }:
                    yield {"event": "done", "data": message["data"]}
                    return
        except asyncio.CancelledError:
            raise
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    return EventSourceResponse(publisher())


# Per-workflow success rates live on the analytics router (/analytics/workflows) so they do
# not collide with the /executions/{execution_id} path parameter.
