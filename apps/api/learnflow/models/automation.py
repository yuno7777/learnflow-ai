"""Workflows, executions, steps, inbound webhook receipts, and support tickets.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file defines the workflow automation and event dispatch data layer:
1. `ExecutionStatus` & `TriggerType`: Lifecycle states and trigger modes for automations.
2. `Workflow`: Workflow configuration linking triggers (e.g. `learner.created`)
   to n8n webhooks or built-in Python templates.
3. `WorkflowExecution`: A single run of a workflow with tenant idempotency keys,
   token consumption, USD cost attribution, and n8n execution IDs.
4. `WorkflowStep`: Fine-grained audit of each step (AI completion, HTTP call, email send).
5. `AutomationEvent`: Internal domain event log.
6. `WebhookReceipt`: Database store for HMAC signatures, enforcing a UNIQUE constraint
   on `signature` to provide cryptographic replay attack protection.
7. `SupportTicket`: Helpdesk ticket triaged by AI with sentiment, urgency, and auto-responses.

===============================================================================
HOW IT CONNECTS TO THE REST OF THE PROJECT:
===============================================================================
- `automation/engine.py` & `automation/runners.py`: Execute workflows and create
  `WorkflowExecution` and `WorkflowStep` records.
- `automation/events.py`: Inserts `AutomationEvent` rows when business actions occur.
- `routers/webhooks.py`: Inserts received HMAC signatures into `WebhookReceipt`
  to reject duplicate/replayed payloads at the database level.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantEntity


def _enum(e, n):
    """Helper to create cross-dialect SQLAlchemy Enum columns."""
    return SAEnum(e, native_enum=False, length=n, values_callable=lambda x: [m.value for m in x])


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------


class ExecutionStatus(str, enum.Enum):
    queued = "queued"  # Dispatched to ARQ background worker
    running = "running"  # In-progress (in n8n or Python runner)
    waiting_approval = "waiting_approval"  # Paused awaiting human sign-off
    succeeded = "succeeded"  # All steps completed successfully
    failed = "failed"  # Halted due to an unrecoverable error
    cancelled = "cancelled"  # Aborted by user


class TriggerType(str, enum.Enum):
    manual = "manual"  # Triggered via Dashboard UI button
    event = "event"  # Triggered by internal domain event (e.g. `learner.created`)
    schedule = "schedule"  # Triggered by worker cron sweep
    webhook = "webhook"  # Triggered by inbound external HTTP POST
    api = "api"  # Triggered via direct API call


# -----------------------------------------------------------------------------
# Workflow Engine Models
# -----------------------------------------------------------------------------


class Workflow(TenantEntity):
    """A configured automation workflow (e.g. 'Learner Onboarding' or 'Inactivity Reminder')."""

    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("organization_id", "key", name="uq_workflow_key"),)

    key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    template: Mapped[str] = mapped_column(String(80), nullable=False)
    trigger_type: Mapped[TriggerType] = mapped_column(_enum(TriggerType, 20), nullable=False)
    trigger_event: Mapped[str | None] = mapped_column(String(80), index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    n8n_webhook_path: Mapped[str | None] = mapped_column(String(200))
    n8n_workflow_id: Mapped[str | None] = mapped_column(String(80))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=2, nullable=False)

    executions: Mapped[list[WorkflowExecution]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )


class WorkflowExecution(TenantEntity):
    """A concrete execution run of a workflow.

    Uses `idempotency_key` unique per organization to guarantee that a network retry
    cannot trigger duplicate workflow runs.
    """

    __tablename__ = "workflow_executions"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_execution_idem"),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[ExecutionStatus] = mapped_column(
        _enum(ExecutionStatus, 24), default=ExecutionStatus.queued, nullable=False, index=True
    )
    trigger_type: Mapped[TriggerType] = mapped_column(_enum(TriggerType, 20), nullable=False)
    triggered_by: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    input_payload: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    output_payload: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    n8n_execution_id: Mapped[str | None] = mapped_column(String(80), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)
    learner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("learners.id", ondelete="SET NULL")
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="SET NULL")
    )

    workflow: Mapped[Workflow] = relationship(back_populates="executions")
    steps: Mapped[list[WorkflowStep]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        order_by="WorkflowStep.position",
        lazy="selectin",
    )


class WorkflowStep(TenantEntity):
    """An individual step within a workflow execution (e.g. 'send_welcome_email',
    'generate_quiz').
    """

    __tablename__ = "workflow_steps"

    execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflow_executions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(40), nullable=False
    )  # "ai" | "http" | "email" | "delay" | "approval"
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    execution: Mapped[WorkflowExecution] = relationship(back_populates="steps")


class AutomationEvent(TenantEntity):
    """Internal domain event log (e.g. `learner.created`, `ticket.created`)."""

    __tablename__ = "automation_events"

    name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    dispatched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    execution_ids: Mapped[list[Any]] = mapped_column(default=list, nullable=False)


# -----------------------------------------------------------------------------
# Security & Support Ticket Models
# -----------------------------------------------------------------------------


class WebhookReceipt(TenantEntity):
    """Stores incoming webhook signatures to prevent replay attacks.

    CRITICAL SECURITY MECHANISM:
    The `signature` column has a UNIQUE constraint. If an attacker replays a valid
    HMAC signature, the database insert throws an IntegrityError, stopping execution immediately.
    """

    __tablename__ = "webhook_receipts"
    __table_args__ = (UniqueConstraint("signature", name="uq_webhook_signature"),)

    source: Mapped[str] = mapped_column(String(40), nullable=False)
    signature: Mapped[str] = mapped_column(String(128), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(120), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)


class SupportTicket(TenantEntity):
    """Helpdesk support ticket created by learners and automatically triaged by AI."""

    __tablename__ = "support_tickets"

    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    learner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("learners.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="open", nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(40), index=True)
    urgency: Mapped[str | None] = mapped_column(String(20))
    sentiment: Mapped[str | None] = mapped_column(String(20))
    ai_confidence: Mapped[float | None] = mapped_column(Float)
    ai_explanation: Mapped[str | None] = mapped_column(Text)
    suggested_response: Mapped[str | None] = mapped_column(Text)
    kb_references: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
