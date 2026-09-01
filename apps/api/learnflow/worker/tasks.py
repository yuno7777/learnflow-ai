"""ARQ background task implementations.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file contains the actual worker task logic executed by ARQ worker threads:
1. `_run()`: Shared lifecycle wrapper managing transactions, initializing `AIGateway`,
   recording job start/progress, committing results, and catching errors so diagnostic
   details are persisted before re-raising for ARQ retry policies.
2. `ingest_document`: Runs the 10-stage RAG ingestion pipeline (`rag/pipeline.py`).
3. `generate_course`: Generates multi-module course curricula (`services/generation.py`).
4. `regenerate_module`: Re-synthesizes a single module from feedback.
5. `generate_assessment`: Generates questions and filters through validation rules.
6. `evaluate_submission`: Evaluates learner submissions and emits `evaluation.needs_review`
   events if confidence drops below 0.75.
7. `run_workflow`: Executes n8n or Python automation workflows (`automation/engine.py`).
8. `transform_content`: Generates FAQs, flashcards, or executive summaries.

===============================================================================
TRANSACTIONAL ERROR CAPTURING:
===============================================================================
If a task crashes, `_run()` catches the exception, marks the `Job` row failed in PostgreSQL,
commits the diagnostic message, and *then* re-raises the exception.
This guarantees that ARQ sees the failure and triggers backoff retries, while the web
UI dashboard immediately displays the exact error message to the user.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..ai.gateway import AIGateway
from ..db import session_scope
from ..logging import get_logger
from ..models import (
    Assessment,
    Course,
    CourseStatus,
    Document,
    Job,
    Module,
    PipelineStage,
    Submission,
    Workflow,
    WorkflowExecution,
)
from ..services import jobs as job_service

log = get_logger("worker")


async def _run(job_id: str, organization_id: str, kind: str, body):
    """Shared job execution wrapper handling session scope, error logging, and ARQ retries."""
    failure: Exception | None = None
    result: Any = None
    async with session_scope() as session:
        job = await job_service.start(session, uuid.UUID(job_id))
        if job is None:
            log.warning("job_missing", job_id=job_id, kind=kind)
            return {"error": "job_missing"}
        gateway = AIGateway(session, uuid.UUID(organization_id))
        try:
            result = await body(session, gateway, job)
        except Exception as exc:
            # Persist failure details inside the transaction before re-raising
            await job_service.fail(session, job, f"{type(exc).__name__}: {exc}")
            failure = exc
        else:
            await job_service.finish(session, job, result=result)

    if failure is not None:
        log.error("job_failed", kind=kind, job_id=job_id, error=str(failure))
        raise failure  # Re-raise so ARQ registers the failure and schedules retry
    return result


async def ingest_document(
    ctx: dict, job_id: str, organization_id: str, *, document_id: str, from_stage: str | None = None
) -> dict[str, Any]:
    """Background task: Ingests, parses, chunks, embeds, and indexes an uploaded document."""
    from ..rag.pipeline import run_pipeline

    async def body(session, gateway, job: Job) -> dict[str, Any]:
        document = await session.get(Document, uuid.UUID(document_id))
        if document is None or str(document.organization_id) != organization_id:
            raise ValueError("Document not found in this organization")
        await job_service.progress(session, job, 0.05, "Starting ingestion pipeline")
        document = await run_pipeline(
            session,
            document,
            gateway,
            from_stage=PipelineStage(from_stage) if from_stage else None,
        )
        if document.status.value == "failed":
            raise RuntimeError(document.error or "Ingestion failed")
        return {
            "document_id": str(document.id),
            "status": document.status.value,
            "words": document.word_count,
        }

    return await _run(job_id, organization_id, "ingest_document", body)


async def generate_course(
    ctx: dict,
    job_id: str,
    organization_id: str,
    *,
    course_id: str,
    subject: str,
    module_count: int,
    audience: str,
) -> dict[str, Any]:
    """Background task: Generates a full course curriculum from source documents."""
    from ..services.generation import generate_course as build

    async def body(session, gateway, job: Job) -> dict[str, Any]:
        course = await session.get(Course, uuid.UUID(course_id))
        if course is None or str(course.organization_id) != organization_id:
            raise ValueError("Course not found in this organization")
        await job_service.progress(session, job, 0.2, "Sampling source material")
        try:
            course = await build(
                session,
                gateway,
                course,
                subject=subject,
                module_count=module_count,
                audience=audience,
            )
        except Exception:
            course.status = CourseStatus.failed
            raise
        return {"course_id": str(course.id), "modules": len(course.modules)}

    return await _run(job_id, organization_id, "generate_course", body)


async def regenerate_module(
    ctx: dict,
    job_id: str,
    organization_id: str,
    *,
    course_id: str,
    module_id: str,
    instructions: str | None = None,
) -> dict[str, Any]:
    """Background task: Rewrites a single course module based on instructor feedback."""
    from ..services.generation import regenerate_module as regen

    async def body(session, gateway, job: Job) -> dict[str, Any]:
        course = await session.get(Course, uuid.UUID(course_id))
        module = await session.get(Module, uuid.UUID(module_id))
        if course is None or module is None or str(course.organization_id) != organization_id:
            raise ValueError("Course or module not found in this organization")
        await regen(session, gateway, course, module, instructions)
        return {"module_id": str(module.id), "lessons": len(module.lessons)}

    return await _run(job_id, organization_id, "regenerate_module", body)


async def generate_assessment(
    ctx: dict,
    job_id: str,
    organization_id: str,
    *,
    assessment_id: str,
    document_ids: list[str],
    question_count: int,
    question_types: list[str],
    topics: list[str],
) -> dict[str, Any]:
    """Background task: Generates quiz questions and applies validation quality filters."""
    from ..services.generation import generate_assessment as build

    async def body(session, gateway, job: Job) -> dict[str, Any]:
        assessment = await session.get(Assessment, uuid.UUID(assessment_id))
        if assessment is None or str(assessment.organization_id) != organization_id:
            raise ValueError("Assessment not found in this organization")
        await job_service.progress(session, job, 0.3, "Generating questions")
        assessment = await build(
            session,
            gateway,
            assessment,
            document_ids=[uuid.UUID(d) for d in document_ids],
            question_count=question_count,
            question_types=question_types,
            topics=topics,
        )
        return {
            "assessment_id": str(assessment.id),
            "accepted": len(assessment.validation_report.get("accepted", [])),
            "rejected": assessment.validation_report.get("rejected", 0),
        }

    return await _run(job_id, organization_id, "generate_assessment", body)


async def evaluate_submission(
    ctx: dict, job_id: str, organization_id: str, *, submission_id: str
) -> dict[str, Any]:
    """Background task: Auto-grades student submissions and triggers review events if needed."""
    from ..automation import events
    from ..services.evaluation import evaluate_submission as evaluate

    async def body(session, gateway, job: Job) -> dict[str, Any]:
        submission = await session.get(Submission, uuid.UUID(submission_id))
        if submission is None or str(submission.organization_id) != organization_id:
            raise ValueError("Submission not found in this organization")
        evaluation = await evaluate(session, gateway, submission)
        # If confidence is low (<0.75), dispatch an event to alert human reviewers
        if evaluation.requires_human_review:
            await events.emit(
                session,
                submission.organization_id,
                "evaluation.needs_review",
                {"evaluation_id": str(evaluation.id), "submission_id": str(submission.id)},
                idempotency_key=str(evaluation.id),
            )
        return {
            "evaluation_id": str(evaluation.id),
            "score": evaluation.score,
            "confidence": evaluation.confidence,
            "needs_review": evaluation.requires_human_review,
        }

    return await _run(job_id, organization_id, "evaluate_submission", body)


async def run_workflow(
    ctx: dict, job_id: str, organization_id: str, *, execution_id: str
) -> dict[str, Any]:
    """Background task: Executes an automation workflow execution via n8n or Python fallback."""
    from ..automation.engine import run

    async def body(session, gateway, job: Job) -> dict[str, Any]:
        execution = await session.get(WorkflowExecution, uuid.UUID(execution_id))
        if execution is None or str(execution.organization_id) != organization_id:
            raise ValueError("Execution not found in this organization")
        workflow = await session.get(Workflow, execution.workflow_id)
        execution = await run(session, execution, workflow, gateway)
        if execution.status.value == "failed":
            raise RuntimeError(execution.error or "Workflow failed")
        return {"execution_id": str(execution.id), "status": execution.status.value}

    return await _run(job_id, organization_id, "run_workflow", body)


async def transform_content(
    ctx: dict,
    job_id: str,
    organization_id: str,
    *,
    document_ids: list[str],
    output_type: str,
    instructions: str = "",
) -> dict[str, Any]:
    """Background task: Repurposes training documents into FAQs, flashcards, or summaries."""
    from ..ai.base import ChatMessage
    from ..ai.prompts import CONTENT_TRANSFORM
    from ..ai.structured import TransformedContent
    from ..services.generation import _source_context

    async def body(session, gateway, job: Job) -> dict[str, Any]:
        context = await _source_context(
            session, uuid.UUID(organization_id), [uuid.UUID(d) for d in document_ids]
        )
        system, user = CONTENT_TRANSFORM.render(
            context=context[:16000], output_type=output_type, instructions=instructions
        )
        result = await gateway.complete(
            f"transform_{output_type}",
            [ChatMessage("system", system), ChatMessage("user", user)],
            prompt=CONTENT_TRANSFORM,
            schema=TransformedContent,
            temperature=0.4,
            max_tokens=3000,
        )
        content: TransformedContent = result.parsed
        return {
            "output_type": output_type,
            "title": content.title,
            "body": content.body,
            "bullet_points": content.bullet_points,
            "model": result.model,
            "estimated_cost_usd": result.estimated_cost_usd,
        }

    return await _run(job_id, organization_id, "transform_content", body)
