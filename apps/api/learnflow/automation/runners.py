"""Built-in Python workflow runners (fallback and local execution engine).

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file implements the built-in Python workflow execution handlers.
Each runner mirrors the exact steps of the corresponding exportable n8n workflow:
1. `learner_onboarding`: Generates welcome AI message, enrolls student, sends welcome email,
   builds onboarding checklist, and schedules reminders.
2. `inactive_reminder`: Queries quiet learners past inactivity threshold and sends AI nudges.
3. `ticket_triage`: Retrieves knowledge base citations, classifies ticket sentiment/urgency,
   drafts an AI response, and sets escalation flags for low-confidence tickets.
4. `document_processed`: Sends completion notifications to admins.
5. `course_completion`: Delivers congratulatory completion emails.
6. `assessment_review`: Alerts instructors when evaluations fall below 0.75 confidence.
7. `weekly_summary`: Aggregates active learners, tokens, and USD costs into a weekly digest.

===============================================================================
WHY DUAL-EXECUTION (n8n Webhooks + Python Runners)?
===============================================================================
In production, organizations can visually customize workflows in n8n.
However, if the n8n cluster is offline, unconfigured, or during CI/CD test runs,
`automation/engine.py` seamlessly falls back to these deterministic Python runners,
guaranteeing that core LMS operations never halt.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.base import ChatMessage
from ..ai.gateway import AIGateway
from ..ai.prompts import REMINDER_MESSAGE, TICKET_TRIAGE, WELCOME_MESSAGE
from ..ai.structured import TICKET_CATEGORIES, TicketTriage
from ..models import (
    Course,
    Document,
    Enrollment,
    Evaluation,
    EvaluationStatus,
    Learner,
    Organization,
    SupportTicket,
    Workflow,
    WorkflowExecution,
)
from ..rag.retrieval import build_context, retrieve
from .email import get_email_adapter
from .engine import StepRecorder

# Standard onboarding checklist tasks
ONBOARDING_CHECKLIST = [
    "Complete your profile",
    "Read the welcome guide",
    "Start your first module",
    "Introduce yourself to your cohort",
]


async def _require_learner(session: AsyncSession, execution: WorkflowExecution) -> Learner:
    """Helper to validate and retrieve the target Learner from database."""
    learner_id = execution.learner_id or execution.input_payload.get("learner_id")
    if not learner_id:
        raise ValueError("Workflow payload has no learner_id")
    learner = await session.get(Learner, uuid.UUID(str(learner_id)))
    if learner is None or learner.organization_id != execution.organization_id:
        raise ValueError("Learner not found in this organization")
    return learner


# -----------------------------------------------------------------------------
# 1. Learner Onboarding Runner
# -----------------------------------------------------------------------------
async def learner_onboarding(
    session: AsyncSession,
    execution: WorkflowExecution,
    workflow: Workflow,
    gateway: AIGateway,
    recorder: StepRecorder,
) -> dict[str, Any]:
    """Executes the 6-step onboarding workflow for new learners."""
    result: dict[str, Any] = {}

    # Step 1: Validation
    async with recorder.step("validate_learner", "validation") as step:
        learner = await _require_learner(session, execution)
        org = await session.get(Organization, execution.organization_id)
        course_id = execution.course_id or execution.input_payload.get("course_id")
        course = await session.get(Course, uuid.UUID(str(course_id))) if course_id else None
        if course is not None and course.organization_id != execution.organization_id:
            raise ValueError("Course belongs to another organization")
        step.output = {"learner": learner.email, "course": course.title if course else None}

    # Step 2: AI Welcome Message Generation
    async with recorder.step("generate_welcome_message", "ai") as step:
        system, user = WELCOME_MESSAGE.render(
            learner_name=learner.full_name,
            organization=org.name if org else "your organization",
            course=course.title if course else "your learning path",
        )
        ai = await gateway.complete(
            "workflow_welcome_message",
            [ChatMessage("system", system), ChatMessage("user", user)],
            prompt=WELCOME_MESSAGE,
            temperature=0.4,
            max_tokens=400,
        )
        result["welcome_message"] = ai.text
        step.output = {"chars": len(ai.text), "model": ai.model, "cost_usd": ai.estimated_cost_usd}

    # Step 3: Database Enrollment
    async with recorder.step("enrol_learner", "database") as step:
        if course is not None:
            existing = await session.scalar(
                select(Enrollment).where(
                    Enrollment.learner_id == learner.id, Enrollment.course_id == course.id
                )
            )
            if existing is None:
                session.add(
                    Enrollment(
                        organization_id=execution.organization_id,
                        learner_id=learner.id,
                        course_id=course.id,
                    )
                )
                await session.flush()
            step.output = {"enrolled": True, "course_id": str(course.id)}
        else:
            step.output = {"enrolled": False, "reason": "no course supplied"}

    # Step 4: Email Dispatch
    async with recorder.step("send_welcome_email", "email") as step:
        sent = await get_email_adapter().send(
            learner.email,
            f"Welcome to {org.name if org else 'LearnFlow'}",
            result["welcome_message"],
        )
        step.output = {"to": sent.to, "adapter": get_email_adapter().name}

    # Step 5: Onboarding Checklist Initialization
    async with recorder.step("create_onboarding_checklist", "database") as step:
        learner.profile = {
            **learner.profile,
            "onboarding_checklist": [{"task": t, "done": False} for t in ONBOARDING_CHECKLIST],
        }
        learner.status = "active"
        learner.last_active_at = datetime.now(UTC)
        await session.flush()
        step.output = {"items": len(ONBOARDING_CHECKLIST)}

    # Step 6: Schedule Future Reminders
    async with recorder.step("schedule_reminders", "schedule") as step:
        due = [(datetime.now(UTC) + timedelta(days=d)).isoformat() for d in (3, 7, 14)]
        learner.profile = {**learner.profile, "reminder_schedule": due}
        await session.flush()
        step.output = {"scheduled": due}

    result["checklist"] = ONBOARDING_CHECKLIST
    result["learner_id"] = str(learner.id)
    return result


# -----------------------------------------------------------------------------
# 2. Inactive Learner Reminder Runner
# -----------------------------------------------------------------------------
async def inactive_reminder(
    session: AsyncSession,
    execution: WorkflowExecution,
    workflow: Workflow,
    gateway: AIGateway,
    recorder: StepRecorder,
) -> dict[str, Any]:
    """Sweeps for inactive students and sends AI-personalized re-engagement emails."""
    config = workflow.config or {}
    threshold = int(config.get("inactivity_days", 14))
    max_reminders = int(config.get("max_reminders", 3))
    tone = str(config.get("tone", "supportive"))
    cutoff = datetime.now(UTC) - timedelta(days=threshold)

    # Step 1: Find Inactive Learners
    async with recorder.step("find_inactive_learners", "database") as step:
        candidates = list(
            (
                await session.execute(
                    select(Learner).where(
                        Learner.organization_id == execution.organization_id,
                        Learner.status == "active",
                        Learner.reminders_sent < max_reminders,
                        (Learner.last_active_at.is_(None)) | (Learner.last_active_at < cutoff),
                    )
                )
            )
            .scalars()
            .all()
        )
        step.output = {"count": len(candidates), "threshold_days": threshold}

    # Step 2: Generate and Send Personalized Nudges
    sent, failed = [], []
    for learner in candidates[: int(config.get("batch_limit", 25))]:
        try:
            async with recorder.step(f"remind:{learner.email}", "ai") as step:
                enrollment = await session.scalar(
                    select(Enrollment).where(Enrollment.learner_id == learner.id)
                )
                course = await session.get(Course, enrollment.course_id) if enrollment else None
                days = (
                    (datetime.now(UTC) - learner.last_active_at).days
                    if learner.last_active_at
                    else threshold
                )
                system, user = REMINDER_MESSAGE.render(
                    learner_name=learner.full_name,
                    days_inactive=str(days),
                    course=course.title if course else "your course",
                    progress=str(int((enrollment.progress if enrollment else 0) * 100)),
                    tone=tone,
                    reminder_number=str(learner.reminders_sent + 1),
                )
                ai = await gateway.complete(
                    "workflow_reminder",
                    [ChatMessage("system", system), ChatMessage("user", user)],
                    prompt=REMINDER_MESSAGE,
                    temperature=0.5,
                    max_tokens=300,
                )
                await get_email_adapter().send(learner.email, "Pick up where you left off", ai.text)
                learner.reminders_sent += 1
                await session.flush()
                sent.append(learner.email)
                step.output = {"days_inactive": days, "reminder_number": learner.reminders_sent}
        except Exception as exc:
            failed.append({"learner": learner.email, "error": str(exc)[:200]})

    return {"sent": sent, "failed": failed, "threshold_days": threshold}


# -----------------------------------------------------------------------------
# 3. Support Ticket Triage Runner
# -----------------------------------------------------------------------------
async def ticket_triage(
    session: AsyncSession,
    execution: WorkflowExecution,
    workflow: Workflow,
    gateway: AIGateway,
    recorder: StepRecorder,
) -> dict[str, Any]:
    """Classifies support tickets and drafts grounded responses."""
    ticket_id = execution.input_payload.get("ticket_id")
    if not ticket_id:
        raise ValueError("Workflow payload has no ticket_id")
    ticket = await session.get(SupportTicket, uuid.UUID(str(ticket_id)))
    if ticket is None or ticket.organization_id != execution.organization_id:
        raise ValueError("Ticket not found in this organization")

    # Step 1: Retrieve Grounded Passages
    async with recorder.step("retrieve_knowledge", "rag") as step:
        retrieval = await retrieve(
            session,
            gateway,
            f"{ticket.subject}\n{ticket.body}",
            organization_id=execution.organization_id,
            top_k=4,
        )
        step.output = {"citations": len(retrieval.citations), "confidence": retrieval.confidence}

    # Step 2: AI Classification & Response Drafting
    async with recorder.step("classify_ticket", "ai") as step:
        system, user = TICKET_TRIAGE.render(
            subject=ticket.subject,
            body=ticket.body[:6000],
            context=build_context(retrieval.citations) or "(no relevant knowledge base content)",
            categories=", ".join(TICKET_CATEGORIES),
        )
        ai = await gateway.complete(
            "ticket_triage",
            [ChatMessage("system", system), ChatMessage("user", user)],
            prompt=TICKET_TRIAGE,
            schema=TicketTriage,
            temperature=0.1,
            retrieved_chunk_ids=retrieval.chunk_ids,
        )
        triage = cast(TicketTriage, ai.parsed)
        step.output = {
            "category": triage.category,
            "urgency": triage.urgency,
            "confidence": triage.confidence,
        }

    # Step 3: Database Ticket Update & Escalation Routing
    async with recorder.step("route_ticket", "database") as step:
        low_confidence = triage.confidence < float(
            (workflow.config or {}).get("min_confidence", 0.6)
        )
        ticket.category = triage.category
        ticket.urgency = triage.urgency
        ticket.sentiment = triage.sentiment
        ticket.ai_confidence = triage.confidence
        ticket.ai_explanation = triage.explanation
        ticket.suggested_response = triage.suggested_response
        ticket.kb_references = [
            {"document": c.document_name, "chunk_id": c.chunk_id, "score": c.score}
            for c in retrieval.citations
        ]
        ticket.escalated = triage.escalate or low_confidence
        ticket.status = "escalated" if ticket.escalated else "triaged"
        await session.flush()
        step.output = {"escalated": ticket.escalated, "status": ticket.status}

    return {
        "ticket_id": str(ticket.id),
        "category": triage.category,
        "urgency": triage.urgency,
        "escalated": ticket.escalated,
        "confidence": triage.confidence,
    }


# -----------------------------------------------------------------------------
# 4. Other Workflow Runners
# -----------------------------------------------------------------------------


async def document_processed(
    session: AsyncSession,
    execution: WorkflowExecution,
    workflow: Workflow,
    gateway: AIGateway,
    recorder: StepRecorder,
) -> dict[str, Any]:
    """Notifies administrators when document indexing finishes."""
    document_id = execution.input_payload.get("document_id")
    async with recorder.step("load_document", "database") as step:
        document = await session.get(Document, uuid.UUID(str(document_id))) if document_id else None
        if document is None or document.organization_id != execution.organization_id:
            raise ValueError("Document not found in this organization")
        step.output = {"filename": document.filename, "status": document.status.value}

    async with recorder.step("notify_admins", "email") as step:
        admins = (workflow.config or {}).get("notify", [])
        for address in admins:
            await get_email_adapter().send(
                address,
                f"Document indexed: {document.filename}",
                f"{document.filename} finished indexing with "
                f"{document.word_count or 0} words across {document.page_count or 0} pages.",
            )
        step.output = {"notified": admins}
    return {"document_id": str(document.id), "status": document.status.value}


async def course_completion(
    session: AsyncSession,
    execution: WorkflowExecution,
    workflow: Workflow,
    gateway: AIGateway,
    recorder: StepRecorder,
) -> dict[str, Any]:
    """Sends congratulatory completion certificates to learners."""
    async with recorder.step("load_enrollment", "database") as step:
        learner = await _require_learner(session, execution)
        course_id = execution.course_id or execution.input_payload.get("course_id")
        course = await session.get(Course, uuid.UUID(str(course_id))) if course_id else None
        step.output = {"learner": learner.email, "course": course.title if course else None}

    async with recorder.step("send_certificate_email", "email") as step:
        await get_email_adapter().send(
            learner.email,
            f"You completed {course.title if course else 'your course'}",
            "Congratulations on completing the course. Your certificate is attached in the portal.",
        )
        step.output = {"to": learner.email}
    return {"learner_id": str(learner.id), "course_id": str(course.id) if course else None}


async def assessment_review(
    session: AsyncSession,
    execution: WorkflowExecution,
    workflow: Workflow,
    gateway: AIGateway,
    recorder: StepRecorder,
) -> dict[str, Any]:
    """Alerts instructors to review low-confidence AI evaluations."""
    async with recorder.step("collect_pending_reviews", "database") as step:
        pending = list(
            (
                await session.execute(
                    select(Evaluation).where(
                        Evaluation.organization_id == execution.organization_id,
                        Evaluation.status == EvaluationStatus.needs_review,
                    )
                )
            )
            .scalars()
            .all()
        )
        step.output = {"pending": len(pending)}

    async with recorder.step("notify_reviewers", "email") as step:
        recipients = (workflow.config or {}).get("reviewers", [])
        if pending and recipients:
            body = "\n".join(
                f"- evaluation {e.id}: score {e.score} (confidence {e.confidence})" for e in pending
            )
            for address in recipients:
                await get_email_adapter().send(
                    address, f"{len(pending)} AI evaluations need review", body
                )
        step.output = {"notified": recipients, "count": len(pending)}
    return {"pending_reviews": len(pending)}


async def weekly_summary(
    session: AsyncSession,
    execution: WorkflowExecution,
    workflow: Workflow,
    gateway: AIGateway,
    recorder: StepRecorder,
) -> dict[str, Any]:
    """Compiles a weekly usage and cost digest for the organization."""
    from ..models import LLMCall

    since = datetime.now(UTC) - timedelta(days=7)
    async with recorder.step("collect_metrics", "database") as step:
        learners = await session.scalar(
            select(func.count())
            .select_from(Learner)
            .where(Learner.organization_id == execution.organization_id)
        )
        calls, tokens, cost = (
            await session.execute(
                select(
                    func.count(LLMCall.id),
                    func.coalesce(func.sum(LLMCall.input_tokens + LLMCall.output_tokens), 0),
                    func.coalesce(func.sum(LLMCall.estimated_cost_usd), 0),
                ).where(
                    LLMCall.organization_id == execution.organization_id,
                    LLMCall.created_at >= since,
                )
            )
        ).one()
        metrics = {
            "learners": int(learners or 0),
            "ai_requests": int(calls or 0),
            "tokens": int(tokens or 0),
            "estimated_cost_usd": float(cost or 0),
        }
        step.output = metrics

    async with recorder.step("email_summary", "email") as step:
        recipients = (workflow.config or {}).get("recipients", [])
        body = "\n".join(f"{k}: {v}" for k, v in metrics.items())
        for address in recipients:
            await get_email_adapter().send(address, "LearnFlow weekly summary", body)
        step.output = {"notified": recipients}
    return metrics


# Registry mapping template names to execution functions
RUNNERS: dict[str, Any] = {
    "learner_onboarding": learner_onboarding,
    "inactive_reminder": inactive_reminder,
    "ticket_triage": ticket_triage,
    "document_processed": document_processed,
    "course_completion": course_completion,
    "assessment_review": assessment_review,
    "weekly_summary": weekly_summary,
}

# Template metadata definitions for workflow provisioning
TEMPLATES: dict[str, dict[str, Any]] = {
    "learner_onboarding": {
        "name": "New learner onboarding",
        "trigger_event": "learner.created",
        "description": "Welcome message, enrolment, email, checklist and reminder schedule.",
        "config": {},
    },
    "inactive_reminder": {
        "name": "Inactive learner reminder",
        "trigger_event": "schedule.daily",
        "description": "Finds quiet learners and sends personalised nudges.",
        "config": {"inactivity_days": 14, "max_reminders": 3, "tone": "supportive"},
    },
    "ticket_triage": {
        "name": "Support ticket triage",
        "trigger_event": "ticket.created",
        "description": "Classifies, scores urgency and drafts a KB-grounded reply.",
        "config": {"min_confidence": 0.6},
    },
    "document_processed": {
        "name": "Document processing notification",
        "trigger_event": "document.indexed",
        "description": "Notifies admins when ingestion completes.",
        "config": {"notify": []},
    },
    "course_completion": {
        "name": "Course completion notification",
        "trigger_event": "course.completed",
        "description": "Congratulates the learner and issues the certificate email.",
        "config": {},
    },
    "assessment_review": {
        "name": "Assessment review reminder",
        "trigger_event": "evaluation.needs_review",
        "description": "Pings reviewers about low-confidence AI evaluations.",
        "config": {"reviewers": []},
    },
    "weekly_summary": {
        "name": "Weekly analytics summary",
        "trigger_event": "schedule.weekly",
        "description": "Emails a usage and cost digest.",
        "config": {"recipients": []},
    },
}
