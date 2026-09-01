"""Workflow execution engine and n8n callback lifecycle manager.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file is the core runtime engine for all workflow automations:
1. `StepRecorder`: Async context manager that records individual `WorkflowStep`
   database rows and streams step transitions over Redis Pub/Sub (`learnflow:execution:{id}`).
2. `create_execution`: Creates `WorkflowExecution` records with tenant-unique idempotency keys.
3. `run`: The execution dispatcher:
   - Primary Path: Sends HMAC-signed webhook to n8n container (`n8n_client.trigger_webhook()`).
   - Fallback Path: If n8n is offline or unconfigured, catches `N8NUnavailable` and invokes
     the corresponding Python runner (`automation/runners.py`).
4. `_attach_costs`: Aggregates all `llm_calls` made during this execution by explicit foreign key
   (`workflow_execution_id == execution.id`), preventing attribution race conditions.
5. `apply_callback`: Idempotently updates the execution and records step outputs when n8n
   fires its signed completion callback.

===============================================================================
WHY FOREIGN KEY COST ATTRIBUTION MATTERS (Data Accuracy):
===============================================================================
Attributing LLM costs using time windows (`created_at BETWEEN start AND end`) causes
serious bugs when multiple workflows run concurrently in the same tenant, as each
workflow steals or duplicates the other's token usage. Attaching `workflow_execution_id`
to `LLMCall` guarantees 100% accurate cost attribution.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import AIGateway
from ..config import settings
from ..logging import get_logger
from ..models import (
    ExecutionStatus,
    TriggerType,
    Workflow,
    WorkflowExecution,
    WorkflowStep,
)
from ..redis_client import EXECUTION_CHANNEL, get_redis, redis_enabled
from . import n8n_client

log = get_logger(__name__)


async def publish(execution: WorkflowExecution, event: dict[str, Any]) -> None:
    """Publishes workflow execution updates to Redis Pub/Sub for SSE streaming."""
    if not redis_enabled():
        return
    try:
        await get_redis().publish(
            EXECUTION_CHANNEL.format(execution_id=execution.id),
            json.dumps({"execution_id": str(execution.id), **event}, default=str),
        )
    except Exception:
        log.debug("execution_publish_failed", execution_id=str(execution.id))


class StepRecorder:
    """Async context manager for logging and streaming individual workflow steps.

    Usage:
        async with recorder.step("send_email", "email") as step:
            step.output = {"sent": True}
    """

    def __init__(self, session: AsyncSession, execution: WorkflowExecution) -> None:
        self.session = session
        self.execution = execution
        self._position = 0

    @asynccontextmanager
    async def step(self, name: str, kind: str):
        """Creates a WorkflowStep row and tracks start/finish times and errors."""
        self._position += 1
        step = WorkflowStep(
            organization_id=self.execution.organization_id,
            execution_id=self.execution.id,
            position=self._position,
            name=name,
            kind=kind,
            status="running",
            started_at=datetime.now(UTC),
            attempt=self.execution.retry_count + 1,
        )
        self.session.add(step)
        await self.session.flush()
        await publish(self.execution, {"type": "step", "name": name, "status": "running"})
        started = time.perf_counter()
        try:
            yield step
        except Exception as exc:
            step.status = "failed"
            step.error = f"{type(exc).__name__}: {exc}"[:2000]
            step.finished_at = datetime.now(UTC)
            step.duration_ms = int((time.perf_counter() - started) * 1000)
            await self.session.flush()
            await publish(
                self.execution,
                {"type": "step", "name": name, "status": "failed", "error": str(exc)},
            )
            raise
        step.status = step.status if step.status != "running" else "succeeded"
        step.finished_at = datetime.now(UTC)
        step.duration_ms = int((time.perf_counter() - started) * 1000)
        await self.session.flush()
        await publish(
            self.execution,
            {"type": "step", "name": name, "status": step.status, "output": step.output},
        )


async def create_execution(
    session: AsyncSession,
    workflow: Workflow,
    *,
    payload: dict[str, Any],
    trigger_type: TriggerType = TriggerType.manual,
    triggered_by: str | None = None,
    idempotency_key: str | None = None,
    learner_id: uuid.UUID | None = None,
    course_id: uuid.UUID | None = None,
) -> tuple[WorkflowExecution, bool]:
    """Creates a new WorkflowExecution row or returns existing one matching idempotency key."""
    key = idempotency_key or uuid.uuid4().hex
    existing = await session.scalar(
        select(WorkflowExecution).where(
            WorkflowExecution.organization_id == workflow.organization_id,
            WorkflowExecution.idempotency_key == key,
        )
    )
    if existing is not None:
        return existing, False

    execution = WorkflowExecution(
        organization_id=workflow.organization_id,
        workflow_id=workflow.id,
        status=ExecutionStatus.queued,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        idempotency_key=key,
        input_payload=payload,
        learner_id=learner_id,
        course_id=course_id,
    )
    session.add(execution)
    await session.flush()
    return execution, True


async def run(
    session: AsyncSession,
    execution: WorkflowExecution,
    workflow: Workflow,
    gateway: AIGateway,
) -> WorkflowExecution:
    """Executes a workflow execution through n8n or the built-in Python runner."""
    from .runners import RUNNERS

    gateway.workflow_execution_id = execution.id
    execution.status = ExecutionStatus.running
    execution.started_at = execution.started_at or datetime.now(UTC)
    execution.error = None
    await session.flush()
    await publish(execution, {"type": "status", "status": "running"})

    recorder = StepRecorder(session, execution)
    started = time.perf_counter()

    try:
        # Primary Path: Dispatch signed webhook to external n8n cluster
        if workflow.n8n_webhook_path:
            async with recorder.step("dispatch_to_n8n", "http") as step:
                payload = {
                    "execution_id": str(execution.id),
                    "organization_id": str(execution.organization_id),
                    "workflow_key": workflow.key,
                    "callback_url": f"{settings.public_api_base_url}"
                    f"{settings.api_v1_prefix}/webhooks/n8n/callback",
                    "config": workflow.config,
                    "data": execution.input_payload,
                }
                try:
                    response = await n8n_client.trigger_webhook(workflow.n8n_webhook_path, payload)
                    step.output = {"engine": "n8n", "response": response}
                    execution.n8n_execution_id = (
                        str(response.get("executionId") or response.get("execution_id") or "")
                        or None
                    )
                    # Handed off to n8n; inbound callback will mark execution completed
                    execution.output_payload = {"handoff": "n8n", **response}
                    await session.flush()
                    await publish(
                        execution, {"type": "status", "status": "running", "engine": "n8n"}
                    )
                    return execution
                except n8n_client.N8NUnavailable as exc:
                    step.status = "succeeded"
                    step.output = {"engine": "builtin", "reason": str(exc)[:300]}
                    log.warning("n8n_unavailable_falling_back", workflow=workflow.key)

        # Fallback Path: Execute Python runner locally
        runner = RUNNERS.get(workflow.template)
        if runner is None:
            raise ValueError(f"No runner for template '{workflow.template}'")
        output = await runner(session, execution, workflow, gateway, recorder)
        execution.output_payload = output
        execution.status = ExecutionStatus.succeeded
    except Exception as exc:
        execution.status = ExecutionStatus.failed
        execution.error = f"{type(exc).__name__}: {exc}"[:2000]
        log.exception("workflow_failed", workflow=workflow.key, execution_id=str(execution.id))
    finally:
        execution.finished_at = datetime.now(UTC)
        execution.duration_ms = int((time.perf_counter() - started) * 1000)
        await _attach_costs(session, execution)
        await session.flush()
        await publish(
            execution,
            {
                "type": "status",
                "status": execution.status.value,
                "duration_ms": execution.duration_ms,
                "error": execution.error,
            },
        )
    return execution


async def _attach_costs(session: AsyncSession, execution: WorkflowExecution) -> None:
    """Attaches total token spend and USD cost from `llm_calls` to this execution row."""
    from ..models import LLMCall

    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(LLMCall.input_tokens), 0),
                func.coalesce(func.sum(LLMCall.output_tokens), 0),
                func.coalesce(func.sum(LLMCall.estimated_cost_usd), 0),
            ).where(
                LLMCall.organization_id == execution.organization_id,
                LLMCall.workflow_execution_id == execution.id,
            )
        )
    ).one()
    execution.input_tokens, execution.output_tokens = int(row[0]), int(row[1])
    execution.estimated_cost_usd = row[2]


async def apply_callback(
    session: AsyncSession,
    execution: WorkflowExecution,
    *,
    status: ExecutionStatus,
    output: dict[str, Any],
    error: str | None,
    steps: list[Any],
    n8n_execution_id: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> WorkflowExecution:
    """Updates an execution from an inbound signed n8n callback.

    Idempotent: Duplicate callback invocations for already-finished executions are safely ignored.
    """
    if execution.status in (ExecutionStatus.succeeded, ExecutionStatus.cancelled):
        return execution

    base = await session.scalar(
        select(func.coalesce(func.max(WorkflowStep.position), 0)).where(
            WorkflowStep.execution_id == execution.id
        )
    )
    for offset, item in enumerate(steps, start=1):
        session.add(
            WorkflowStep(
                organization_id=execution.organization_id,
                execution_id=execution.id,
                position=int(base or 0) + offset,
                name=item.name,
                kind=item.kind,
                status=item.status,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                duration_ms=item.duration_ms,
                output=item.output,
                error=item.error,
                attempt=execution.retry_count + 1,
            )
        )

    execution.status = status
    execution.output_payload = {**execution.output_payload, **output}
    execution.error = error
    execution.n8n_execution_id = n8n_execution_id or execution.n8n_execution_id
    execution.input_tokens += input_tokens
    execution.output_tokens += output_tokens
    execution.finished_at = datetime.now(UTC)
    if execution.started_at:
        execution.duration_ms = int(
            (execution.finished_at - execution.started_at).total_seconds() * 1000
        )
    await session.flush()
    await publish(execution, {"type": "status", "status": status.value, "source": "n8n"})
    return execution
