"""Analytics, AI usage/cost reporting and the inactivity feed the reminder workflow consumes.

Every figure here is computed from real rows — there are no synthetic or placeholder series.
An empty organization legitimately reports zeros.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, cast, func, select

from ..deps import RepoDep, SessionDep, require
from ..models import (
    Course,
    Document,
    DocumentStatus,
    Enrollment,
    Evaluation,
    Learner,
    LLMCall,
    Submission,
    SupportTicket,
    UsageRecord,
    Workflow,
    WorkflowExecution,
    as_utc,
)
from ..models.automation import ExecutionStatus
from ..schemas.analytics import (
    InactiveLearner,
    InactiveLearnersResponse,
    ModelUsageRow,
    OperationUsageRow,
    OverviewMetrics,
    Timeseries,
    TimeseriesPoint,
    UsageSummary,
    WorkflowStats,
)

router = APIRouter(
    prefix="/analytics", tags=["analytics"], dependencies=[Depends(require("analytics:read"))]
)

ACTIVE_WINDOW_DAYS = 14


def _days_since(now: datetime, value: datetime | None) -> int | None:
    normalised = as_utc(value)
    return (now - normalised).days if normalised is not None else None


def _window(date_from: datetime | None, date_to: datetime | None) -> tuple[datetime, datetime]:
    end = date_to or datetime.now(UTC)
    start = date_from or (end - timedelta(days=30))
    return start, end


@router.get("/overview", response_model=OverviewMetrics, summary="Headline dashboard metrics")
async def overview(
    repo: RepoDep,
    session: SessionDep,
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    course_id: uuid.UUID | None = Query(None),
    workspace_id: uuid.UUID | None = Query(None),
) -> OverviewMetrics:
    org = repo.organization_id
    start, end = _window(date_from, date_to)
    active_cutoff = datetime.now(UTC) - timedelta(days=ACTIVE_WINDOW_DAYS)

    learner_where = [Learner.organization_id == org]
    if workspace_id:
        learner_where.append(Learner.workspace_id == workspace_id)

    total_learners = await session.scalar(
        select(func.count()).select_from(Learner).where(*learner_where)
    )
    active_learners = await session.scalar(
        select(func.count())
        .select_from(Learner)
        .where(*learner_where, Learner.last_active_at >= active_cutoff)
    )

    enrollment_where = [Enrollment.organization_id == org]
    if course_id:
        enrollment_where.append(Enrollment.course_id == course_id)
    enrollments = await session.scalar(
        select(func.count()).select_from(Enrollment).where(*enrollment_where)
    )
    completions = await session.scalar(
        select(func.count())
        .select_from(Enrollment)
        .where(*enrollment_where, Enrollment.completed_at.is_not(None))
    )

    avg_score = await session.scalar(
        select(func.avg(Submission.auto_score)).where(
            Submission.organization_id == org, Submission.auto_score.is_not(None)
        )
    )
    open_tickets = await session.scalar(
        select(func.count())
        .select_from(SupportTicket)
        .where(
            SupportTicket.organization_id == org,
            SupportTicket.status.notin_(["resolved", "closed"]),
        )
    )
    documents_indexed = await session.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.organization_id == org, Document.status == DocumentStatus.indexed)
    )

    ai_requests, tokens, cost, avg_llm_latency = (
        await session.execute(
            select(
                func.count(LLMCall.id),
                func.coalesce(func.sum(LLMCall.input_tokens + LLMCall.output_tokens), 0),
                func.coalesce(func.sum(LLMCall.estimated_cost_usd), 0),
                func.avg(LLMCall.latency_ms),
            ).where(
                LLMCall.organization_id == org,
                LLMCall.created_at.between(start, end),
            )
        )
    ).one()

    rag_latency = await session.scalar(
        select(func.avg(LLMCall.latency_ms)).where(
            LLMCall.organization_id == org,
            LLMCall.operation == "rag_answer",
            LLMCall.created_at.between(start, end),
        )
    )

    total_runs, succeeded, failed, avg_duration = (
        await session.execute(
            select(
                func.count(WorkflowExecution.id),
                func.coalesce(
                    func.sum(cast(WorkflowExecution.status == ExecutionStatus.succeeded, Integer)),
                    0,
                ),
                func.coalesce(
                    func.sum(cast(WorkflowExecution.status == ExecutionStatus.failed, Integer)), 0
                ),
                func.avg(WorkflowExecution.duration_ms),
            ).where(
                WorkflowExecution.organization_id == org,
                WorkflowExecution.created_at.between(start, end),
            )
        )
    ).one()

    return OverviewMetrics(
        total_learners=int(total_learners or 0),
        active_learners=int(active_learners or 0),
        course_completion_rate=round((completions or 0) / enrollments, 4) if enrollments else 0.0,
        average_assessment_score=round(float(avg_score), 2) if avg_score is not None else None,
        open_support_tickets=int(open_tickets or 0),
        documents_indexed=int(documents_indexed or 0),
        ai_requests=int(ai_requests or 0),
        tokens_consumed=int(tokens or 0),
        estimated_cost_usd=float(cost or 0),
        workflow_success_rate=round((succeeded or 0) / total_runs, 4) if total_runs else 0.0,
        workflow_failure_rate=round((failed or 0) / total_runs, 4) if total_runs else 0.0,
        average_workflow_duration_ms=float(avg_duration) if avg_duration is not None else None,
        rag_retrieval_latency_ms=float(rag_latency) if rag_latency is not None else None,
        llm_response_latency_ms=float(avg_llm_latency) if avg_llm_latency is not None else None,
    )


@router.get("/timeseries", response_model=Timeseries, summary="Daily series for a single metric")
async def timeseries(
    repo: RepoDep,
    session: SessionDep,
    metric: str = Query(
        "ai_requests", pattern="^(ai_requests|cost|tokens|executions|submissions)$"
    ),
    days: int = Query(30, ge=1, le=180),
) -> Timeseries:
    org = repo.organization_id
    start = datetime.now(UTC) - timedelta(days=days)

    if metric in ("ai_requests", "cost", "tokens"):
        aggregates: dict[str, Any] = {
            "ai_requests": func.count(LLMCall.id),
            "cost": func.coalesce(func.sum(LLMCall.estimated_cost_usd), 0),
            "tokens": func.coalesce(func.sum(LLMCall.input_tokens + LLMCall.output_tokens), 0),
        }
        value = aggregates[metric]
        stmt = (
            select(func.date(LLMCall.created_at), value)
            .where(LLMCall.organization_id == org, LLMCall.created_at >= start)
            .group_by(func.date(LLMCall.created_at))
            .order_by(func.date(LLMCall.created_at))
        )
    elif metric == "executions":
        stmt = (
            select(func.date(WorkflowExecution.created_at), func.count(WorkflowExecution.id))
            .where(WorkflowExecution.organization_id == org, WorkflowExecution.created_at >= start)
            .group_by(func.date(WorkflowExecution.created_at))
            .order_by(func.date(WorkflowExecution.created_at))
        )
    else:
        stmt = (
            select(func.date(Submission.created_at), func.count(Submission.id))
            .where(Submission.organization_id == org, Submission.created_at >= start)
            .group_by(func.date(Submission.created_at))
            .order_by(func.date(Submission.created_at))
        )

    rows = (await session.execute(stmt)).all()
    return Timeseries(
        metric=metric,
        points=[
            TimeseriesPoint(
                date=day if not isinstance(day, str) else datetime.fromisoformat(day).date(),
                value=float(value or 0),
            )
            for day, value in rows
        ],
    )


@router.get("/usage", response_model=UsageSummary, summary="AI usage and cost breakdown")
async def usage(
    repo: RepoDep,
    session: SessionDep,
    days: int = Query(30, ge=1, le=180),
    model: str | None = Query(None),
) -> UsageSummary:
    org = repo.organization_id
    start = datetime.now(UTC) - timedelta(days=days)
    filters = [UsageRecord.organization_id == org, UsageRecord.occurred_at >= start]
    if model:
        filters.append(UsageRecord.model == model)

    totals = (
        await session.execute(
            select(
                func.count(UsageRecord.id),
                func.coalesce(func.sum(UsageRecord.input_tokens), 0),
                func.coalesce(func.sum(UsageRecord.output_tokens), 0),
                func.coalesce(func.sum(UsageRecord.estimated_cost_usd), 0),
            ).where(*filters)
        )
    ).one()

    by_model = [
        ModelUsageRow(
            provider=provider,
            model=model_name,
            requests=int(requests or 0),
            input_tokens=int(inp or 0),
            output_tokens=int(out or 0),
            estimated_cost_usd=float(cost or 0),
            avg_latency_ms=None,
        )
        for provider, model_name, requests, inp, out, cost in (
            await session.execute(
                select(
                    UsageRecord.provider,
                    UsageRecord.model,
                    func.count(UsageRecord.id),
                    func.coalesce(func.sum(UsageRecord.input_tokens), 0),
                    func.coalesce(func.sum(UsageRecord.output_tokens), 0),
                    func.coalesce(func.sum(UsageRecord.estimated_cost_usd), 0),
                )
                .where(*filters)
                .group_by(UsageRecord.provider, UsageRecord.model)
                .order_by(func.sum(UsageRecord.estimated_cost_usd).desc())
            )
        ).all()
    ]

    by_operation = [
        OperationUsageRow(
            operation=operation,
            requests=int(requests or 0),
            failures=int(failures or 0),
            tokens=int(tokens or 0),
            estimated_cost_usd=float(cost or 0),
            avg_latency_ms=float(latency) if latency is not None else None,
        )
        for operation, requests, failures, tokens, cost, latency in (
            await session.execute(
                select(
                    LLMCall.operation,
                    func.count(LLMCall.id),
                    func.coalesce(func.sum(cast(LLMCall.success.is_(False), Integer)), 0),
                    func.coalesce(func.sum(LLMCall.input_tokens + LLMCall.output_tokens), 0),
                    func.coalesce(func.sum(LLMCall.estimated_cost_usd), 0),
                    func.avg(LLMCall.latency_ms),
                )
                .where(LLMCall.organization_id == org, LLMCall.created_at >= start)
                .group_by(LLMCall.operation)
                .order_by(func.count(LLMCall.id).desc())
            )
        ).all()
    ]

    daily = [
        TimeseriesPoint(
            date=day if not isinstance(day, str) else datetime.fromisoformat(day).date(),
            value=float(cost or 0),
            secondary=float(tokens or 0),
        )
        for day, cost, tokens in (
            await session.execute(
                select(
                    func.date(UsageRecord.occurred_at),
                    func.coalesce(func.sum(UsageRecord.estimated_cost_usd), 0),
                    func.coalesce(
                        func.sum(UsageRecord.input_tokens + UsageRecord.output_tokens), 0
                    ),
                )
                .where(*filters)
                .group_by(func.date(UsageRecord.occurred_at))
                .order_by(func.date(UsageRecord.occurred_at))
            )
        ).all()
    ]

    return UsageSummary(
        total_requests=int(totals[0] or 0),
        total_input_tokens=int(totals[1] or 0),
        total_output_tokens=int(totals[2] or 0),
        total_cost_usd=float(totals[3] or 0),
        by_model=by_model,
        by_operation=by_operation,
        daily=daily,
    )


@router.get("/workflows", response_model=list[WorkflowStats], summary="Per-workflow reliability")
async def workflow_stats(repo: RepoDep, session: SessionDep) -> list[WorkflowStats]:
    rows = (
        await session.execute(
            select(
                Workflow.id,
                Workflow.key,
                Workflow.name,
                func.count(WorkflowExecution.id),
                func.coalesce(
                    func.sum(cast(WorkflowExecution.status == ExecutionStatus.succeeded, Integer)),
                    0,
                ),
                func.coalesce(
                    func.sum(cast(WorkflowExecution.status == ExecutionStatus.failed, Integer)), 0
                ),
                func.coalesce(
                    func.sum(cast(WorkflowExecution.status == ExecutionStatus.running, Integer)), 0
                ),
                func.avg(WorkflowExecution.duration_ms),
                func.max(WorkflowExecution.created_at),
            )
            .join(WorkflowExecution, WorkflowExecution.workflow_id == Workflow.id, isouter=True)
            .where(Workflow.organization_id == repo.organization_id)
            .group_by(Workflow.id, Workflow.key, Workflow.name)
            .order_by(Workflow.name)
        )
    ).all()
    return [
        WorkflowStats(
            workflow_id=wid,
            key=key,
            name=name,
            total=int(total or 0),
            succeeded=int(succeeded or 0),
            failed=int(failed or 0),
            running=int(running or 0),
            success_rate=round((succeeded or 0) / total, 4) if total else 0.0,
            avg_duration_ms=float(avg) if avg is not None else None,
            last_run_at=last,
        )
        for wid, key, name, total, succeeded, failed, running, avg, last in rows
    ]


@router.get(
    "/inactive-learners",
    response_model=InactiveLearnersResponse,
    summary="Learners past the inactivity threshold (consumed by the reminder workflow)",
)
async def inactive_learners(
    repo: RepoDep,
    session: SessionDep,
    threshold_days: int = Query(14, ge=1, le=365),
    max_reminders: int = Query(3, ge=1, le=10),
    limit: int = Query(100, ge=1, le=500),
) -> InactiveLearnersResponse:
    cutoff = datetime.now(UTC) - timedelta(days=threshold_days)
    rows = (
        await session.execute(
            select(Learner, Enrollment, Course.title)
            .join(Enrollment, Enrollment.learner_id == Learner.id, isouter=True)
            .join(Course, Course.id == Enrollment.course_id, isouter=True)
            .where(
                Learner.organization_id == repo.organization_id,
                Learner.status == "active",
                Learner.reminders_sent < max_reminders,
                (Learner.last_active_at.is_(None)) | (Learner.last_active_at < cutoff),
            )
            .limit(limit)
        )
    ).all()

    now = datetime.now(UTC)
    return InactiveLearnersResponse(
        threshold_days=threshold_days,
        max_reminders=max_reminders,
        learners=[
            InactiveLearner(
                id=learner.id,
                email=learner.email,
                full_name=learner.full_name,
                days_inactive=_days_since(now, learner.last_active_at),
                reminders_sent=learner.reminders_sent,
                last_active_at=learner.last_active_at,
                course_id=enrollment.course_id if enrollment else None,
                course_title=title,
                progress=enrollment.progress if enrollment else 0.0,
            )
            for learner, enrollment, title in rows
        ],
    )


@router.get("/evaluations", summary="Evaluation review backlog")
async def evaluation_stats(repo: RepoDep, session: SessionDep) -> dict[str, int | float | None]:
    org = repo.organization_id
    total, needing_review, avg_confidence = (
        await session.execute(
            select(
                func.count(Evaluation.id),
                func.coalesce(
                    func.sum(cast(Evaluation.requires_human_review.is_(True), Integer)), 0
                ),
                func.avg(Evaluation.confidence),
            ).where(Evaluation.organization_id == org)
        )
    ).one()
    return {
        "total": int(total or 0),
        "needs_review": int(needing_review or 0),
        "average_confidence": round(float(avg_confidence), 3)
        if avg_confidence is not None
        else None,
    }
