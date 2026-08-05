from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AnalyticsFilters(BaseModel):
    workspace_id: UUID | None = None
    course_id: UUID | None = None
    workflow_id: UUID | None = None
    model: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


class OverviewMetrics(BaseModel):
    total_learners: int
    active_learners: int
    course_completion_rate: float
    average_assessment_score: float | None
    open_support_tickets: int
    documents_indexed: int
    ai_requests: int
    tokens_consumed: int
    estimated_cost_usd: float
    workflow_success_rate: float
    workflow_failure_rate: float
    average_workflow_duration_ms: float | None
    rag_retrieval_latency_ms: float | None
    llm_response_latency_ms: float | None


class TimeseriesPoint(BaseModel):
    date: date
    value: float
    secondary: float | None = None


class Timeseries(BaseModel):
    metric: str
    points: list[TimeseriesPoint]


class ModelUsageRow(BaseModel):
    provider: str
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    avg_latency_ms: float | None


class OperationUsageRow(BaseModel):
    operation: str
    requests: int
    failures: int
    tokens: int
    estimated_cost_usd: float
    avg_latency_ms: float | None


class UsageSummary(BaseModel):
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    by_model: list[ModelUsageRow]
    by_operation: list[OperationUsageRow]
    daily: list[TimeseriesPoint]


class WorkflowStats(BaseModel):
    workflow_id: UUID
    key: str
    name: str
    total: int
    succeeded: int
    failed: int
    running: int
    success_rate: float
    avg_duration_ms: float | None
    last_run_at: datetime | None


class InactiveLearner(BaseModel):
    id: UUID
    email: str
    full_name: str
    days_inactive: int | None
    reminders_sent: int
    last_active_at: datetime | None
    course_id: UUID | None = None
    course_title: str | None = None
    progress: float = 0.0


class InactiveLearnersResponse(BaseModel):
    threshold_days: int
    max_reminders: int
    learners: list[InactiveLearner]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
