from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from ..models import ExecutionStatus, TriggerType
from .common import ORMModel


class WorkflowCreate(BaseModel):
    key: str = Field(min_length=2, max_length=80, pattern="^[a-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=200)
    template: str
    description: str | None = None
    trigger_type: TriggerType = TriggerType.event
    trigger_event: str | None = None
    n8n_webhook_path: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True
    max_retries: int = Field(default=2, ge=0, le=10)


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_enabled: bool | None = None
    config: dict[str, Any] | None = None
    n8n_webhook_path: str | None = None
    max_retries: int | None = Field(default=None, ge=0, le=10)


class WorkflowOut(ORMModel):
    id: UUID
    key: str
    name: str
    description: str | None
    template: str
    trigger_type: TriggerType
    trigger_event: str | None
    is_enabled: bool
    n8n_webhook_path: str | None
    n8n_workflow_id: str | None
    config: dict[str, Any]
    max_retries: int
    created_at: datetime


class WorkflowStepOut(ORMModel):
    id: UUID
    position: int
    name: str
    kind: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    attempt: int
    output: dict[str, Any]
    error: str | None


class ExecutionOut(ORMModel):
    id: UUID
    workflow_id: UUID
    status: ExecutionStatus
    trigger_type: TriggerType
    triggered_by: str | None
    n8n_execution_id: str | None
    retry_count: int
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    learner_id: UUID | None
    course_id: UUID | None
    error: str | None
    created_at: datetime


class ExecutionDetail(ExecutionOut):
    workflow_name: str | None = None
    workflow_key: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    steps: list[WorkflowStepOut] = Field(default_factory=list)


class TriggerRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=120)
    learner_id: UUID | None = None
    course_id: UUID | None = None


class CallbackStep(BaseModel):
    name: str
    kind: str = "external"
    status: str = "succeeded"
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int | None = None


class ExecutionCallback(BaseModel):
    """Body n8n POSTs back to /webhooks/n8n/callback when a workflow finishes a stage."""

    execution_id: UUID
    status: ExecutionStatus
    n8n_execution_id: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    steps: list[CallbackStep] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class TicketCreate(BaseModel):
    subject: str = Field(min_length=3, max_length=300)
    body: str = Field(min_length=5, max_length=20000)
    learner_id: UUID | None = None
    triage: bool = Field(default=True, description="Run AI triage in the background")


class TicketUpdate(BaseModel):
    status: str | None = None
    category: str | None = None
    urgency: str | None = None
    assigned_to: UUID | None = None
    escalated: bool | None = None


class TicketOut(ORMModel):
    id: UUID
    subject: str
    body: str
    learner_id: UUID | None
    status: str
    category: str | None
    urgency: str | None
    sentiment: str | None
    ai_confidence: float | None
    ai_explanation: str | None
    suggested_response: str | None
    kb_references: list[Any]
    escalated: bool
    assigned_to: UUID | None
    resolved_at: datetime | None
    created_at: datetime


class APIKeyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class APIKeyOut(ORMModel):
    id: UUID
    name: str
    prefix: str
    scopes: list[Any]
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class APIKeyCreated(APIKeyOut):
    key: str = Field(description="Shown exactly once — it is stored hashed")


class AuditLogOut(ORMModel):
    id: UUID
    actor_type: str
    actor_label: str | None
    action: str
    resource_type: str
    resource_id: str | None
    request_id: str | None
    ip_address: str | None
    details: dict[str, Any]
    created_at: datetime


class JobOut(ORMModel):
    id: UUID
    kind: str
    status: str
    progress: float
    message: str | None
    resource_type: str | None
    resource_id: str | None
    attempts: int
    max_attempts: int
    error: str | None
    result: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
