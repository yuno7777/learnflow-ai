"""Workflows, executions, steps, inbound webhook receipts and support tickets."""

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
    return SAEnum(e, native_enum=False, length=n, values_callable=lambda x: [m.value for m in x])


class ExecutionStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    waiting_approval = "waiting_approval"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class TriggerType(str, enum.Enum):
    manual = "manual"
    event = "event"
    schedule = "schedule"
    webhook = "webhook"
    api = "api"


class Workflow(TenantEntity):
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
    __tablename__ = "workflow_steps"

    execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflow_executions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # ai|http|email|delay|approval...
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    execution: Mapped[WorkflowExecution] = relationship(back_populates="steps")


class AutomationEvent(TenantEntity):
    """Domain events that workflows subscribe to (learner.created, course.completed, ...)."""

    __tablename__ = "automation_events"

    name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    dispatched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    execution_ids: Mapped[list[Any]] = mapped_column(default=list, nullable=False)


class WebhookReceipt(TenantEntity):
    """Replay protection + idempotency for inbound signed webhooks."""

    __tablename__ = "webhook_receipts"
    __table_args__ = (UniqueConstraint("signature", name="uq_webhook_signature"),)

    source: Mapped[str] = mapped_column(String(40), nullable=False)
    signature: Mapped[str] = mapped_column(String(128), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(120), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)


class SupportTicket(TenantEntity):
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
