"""Demo seed: `python -m learnflow.seed`.

Creates one organization with a full, self-consistent dataset — people, courses, documents,
assessments, submissions, workflow runs, tickets and AI usage — so the dashboard has real
numbers to show on first launch. Idempotent: re-running updates rather than duplicating.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .ai.gateway import AIGateway
from .ai.pricing import estimate_cost
from .automation.runners import TEMPLATES
from .config import settings
from .db import session_scope
from .logging import configure_logging, get_logger
from .models import (
    Assessment,
    Course,
    CourseStatus,
    Difficulty,
    Document,
    DocumentStatus,
    Enrollment,
    Evaluation,
    EvaluationStatus,
    ExecutionStatus,
    KnowledgeBase,
    Learner,
    Lesson,
    LLMCall,
    Module,
    Organization,
    OrganizationMember,
    Question,
    QuestionType,
    Role,
    Submission,
    SupportTicket,
    TriggerType,
    UsageRecord,
    User,
    Workflow,
    WorkflowExecution,
    WorkflowStep,
    Workspace,
)
from .security import hash_password

log = get_logger("seed")
rng = random.Random(20260805)

DEMO_PASSWORD = "LearnFlow!2026"
ORG_SLUG = "acme-training"

PEOPLE = [
    ("admin@learnflow.dev", "Priya Raman", Role.org_admin),
    ("instructor1@learnflow.dev", "Daniel Okafor", Role.instructor),
    ("instructor2@learnflow.dev", "Mei Lin", Role.instructor),
    ("reviewer@learnflow.dev", "Sofia Alvarez", Role.reviewer),
    ("support@learnflow.dev", "Tom Becker", Role.support_agent),
]

LEARNERS = [
    "ana.silva",
    "ben.carter",
    "chloe.dubois",
    "dev.patel",
    "eva.novak",
    "felix.mueller",
    "grace.kim",
    "hassan.ali",
    "ines.costa",
    "jonas.berg",
]

COURSES = [
    ("Remote Work Essentials", "How hybrid working operates at Acme, end to end."),
    ("Information Security Basics", "Everyday security practice for non-technical staff."),
    ("Customer Support Excellence", "Handling learner and customer conversations well."),
]

MODULES = [
    ("Getting set up", ["Explain the remote policy", "Request approval correctly"]),
    ("Working day to day", ["Run an effective standup", "Escalate a blocker"]),
    ("Wrapping up", ["Report time accurately", "Return equipment on exit"]),
]

TICKETS = [
    (
        "Cannot open module 2",
        "The second module shows a spinner and never loads.",
        "technical_problem",
        "high",
        "negative",
    ),
    (
        "Certificate not received",
        "I finished the course last week but no certificate arrived.",
        "certificate_issue",
        "normal",
        "neutral",
    ),
    (
        "Invoice question",
        "We were charged twice for the same seat this month.",
        "payment_issue",
        "high",
        "frustrated",
    ),
    (
        "Where do I find the rubric?",
        "I would like to see how the written answer is graded.",
        "assessment_query",
        "low",
        "neutral",
    ),
    (
        "Great course",
        "Just wanted to say the security module was genuinely useful.",
        "general_learning",
        "low",
        "positive",
    ),
]

SAMPLE_DOCS = {
    "remote-work-policy.md": """# Remote Work Policy

Employees may work remotely up to three days per week. Requests must be approved by a direct
manager at least five business days in advance. The approval code for a remote request is
RW-2291.

## Equipment
The company provides a laptop and a monitor. Equipment must be returned within ten business
days of leaving the company. Damaged equipment is reported to IT using ticket type EQ-DAMAGE.

## Expenses
Home office expenses up to 400 USD per year are reimbursable with a receipt. Internet costs
are not reimbursable under this policy.
""",
    "security-basics.md": """# Information Security Basics

Every employee completes security training within 30 days of joining. Passwords must be at
least 14 characters and stored in the company password manager, never in a spreadsheet.

## Phishing
Report suspected phishing to security@acme.example using the Report Message button. Do not
forward the message. The security team responds within one business hour.

## Data classification
Data is classified as Public, Internal, Confidential or Restricted. Customer records are
Confidential at minimum and may never be copied to personal devices.
""",
    "support-playbook.md": """# Customer Support Playbook

Acknowledge every ticket within two hours during business hours. Use the learner's name and
restate the problem before proposing a fix.

## Escalation
Escalate to a senior agent when the learner mentions a legal issue, a payment dispute, or
when two replies have not resolved the problem. Escalations use priority P2 unless a service
outage is involved, which is P1.

## Tone
Be concise and factual. Never promise a delivery date that engineering has not confirmed.
""",
}


async def seed(reset: bool = False) -> None:
    async with session_scope() as session:
        organization = await session.scalar(
            select(Organization).where(Organization.slug == ORG_SLUG)
        )
        if organization is not None and reset:
            await session.delete(organization)
            await session.flush()
            organization = None
        if organization is None:
            organization = Organization(
                name="Acme Training",
                slug=ORG_SLUG,
                plan="growth",
                settings={"timezone": "Europe/Berlin", "locale": "en"},
            )
            session.add(organization)
            await session.flush()
        org_id = organization.id

        workspace = await session.scalar(
            select(Workspace).where(Workspace.organization_id == org_id)
        )
        if workspace is None:
            workspace = Workspace(
                organization_id=org_id,
                name="People Operations",
                slug="people-ops",
                description="Onboarding, compliance and enablement programmes.",
            )
            session.add(workspace)
            await session.flush()

        # --- people -----------------------------------------------------------------
        users: dict[str, User] = {}
        for email, name, role in PEOPLE:
            user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(email=email, full_name=name, password_hash=hash_password(DEMO_PASSWORD))
                session.add(user)
                await session.flush()
            membership = await session.scalar(
                select(OrganizationMember).where(
                    OrganizationMember.organization_id == org_id,
                    OrganizationMember.user_id == user.id,
                )
            )
            if membership is None:
                session.add(OrganizationMember(organization_id=org_id, user_id=user.id, role=role))
            users[email] = user
        await session.flush()

        now = datetime.now(UTC)
        learners: list[Learner] = []
        for index, handle in enumerate(LEARNERS):
            email = f"{handle}@acme.example.com"
            learner = await session.scalar(
                select(Learner).where(Learner.organization_id == org_id, Learner.email == email)
            )
            if learner is None:
                learner = Learner(
                    organization_id=org_id,
                    email=email,
                    full_name=handle.replace(".", " ").title(),
                    workspace_id=workspace.id,
                    status="active",
                    # A spread of activity so the inactivity workflow has real candidates.
                    last_active_at=now - timedelta(days=[0, 1, 2, 3, 5, 9, 16, 22, 31, 45][index]),
                    external_ref=f"HRIS-{1000 + index}",
                )
                session.add(learner)
            learners.append(learner)
        await session.flush()

        # --- knowledge base ---------------------------------------------------------
        kb = await session.scalar(
            select(KnowledgeBase).where(KnowledgeBase.organization_id == org_id)
        )
        if kb is None:
            kb = KnowledgeBase(
                organization_id=org_id,
                name="Acme Handbook",
                description="Policies, security guidance and the support playbook.",
                workspace_id=workspace.id,
                embedding_model=settings.embedding_model,
                chunk_size_tokens=settings.chunk_size_tokens,
                chunk_overlap_tokens=settings.chunk_overlap_tokens,
            )
            session.add(kb)
            await session.flush()

        from pathlib import Path

        from .rag.extract import checksum
        from .rag.pipeline import run_pipeline

        gateway = AIGateway(session, org_id)
        upload_dir = Path(settings.upload_dir) / str(org_id)
        upload_dir.mkdir(parents=True, exist_ok=True)

        for filename, content in SAMPLE_DOCS.items():
            data = content.encode()
            digest = checksum(data)
            document = await session.scalar(
                select(Document).where(
                    Document.organization_id == org_id, Document.checksum == digest
                )
            )
            if document is not None and document.status == DocumentStatus.indexed:
                continue
            if document is None:
                document = Document(
                    organization_id=org_id,
                    knowledge_base_id=kb.id,
                    filename=filename,
                    content_type="text/markdown",
                    size_bytes=len(data),
                    checksum=digest,
                    storage_path="",
                    uploaded_by=users["admin@learnflow.dev"].id,
                )
                session.add(document)
                await session.flush()
            path = upload_dir / f"{document.id}.md"
            path.write_bytes(data)
            document.storage_path = str(path)
            await session.flush()
            try:
                await run_pipeline(session, document, gateway)
                log.info("seed_document_indexed", filename=filename)
            except Exception as exc:  # a vector store outage must not abort the whole seed
                log.warning("seed_document_failed", filename=filename, error=str(exc))

        # --- courses ----------------------------------------------------------------
        courses: list[Course] = []
        for c_index, (title, description) in enumerate(COURSES):
            course = await session.scalar(
                select(Course).where(Course.organization_id == org_id, Course.title == title)
            )
            if course is None:
                course = Course(
                    organization_id=org_id,
                    title=title,
                    slug=title.lower().replace(" ", "-"),
                    description=description,
                    status=CourseStatus.published if c_index < 2 else CourseStatus.review,
                    workspace_id=workspace.id,
                    knowledge_base_id=kb.id,
                    learning_objectives=[o for _, objectives in MODULES for o in objectives][:4],
                    model_used=settings.llm_model,
                    prompt_template="course_outline",
                    prompt_version=2,
                    published_at=now - timedelta(days=20 - c_index * 5) if c_index < 2 else None,
                    created_by=users["instructor1@learnflow.dev"].id,
                )
                session.add(course)
                await session.flush()
                for m_index, (module_title, objectives) in enumerate(MODULES):
                    module = Module(
                        organization_id=org_id,
                        course_id=course.id,
                        title=module_title,
                        summary=f"{module_title} for {title}.",
                        position=m_index,
                        learning_objectives=objectives,
                    )
                    session.add(module)
                    await session.flush()
                    for l_index, objective in enumerate(objectives):
                        session.add(
                            Lesson(
                                organization_id=org_id,
                                module_id=module.id,
                                title=objective,
                                position=l_index,
                                summary=f"How to {objective.lower()}.",
                                content=f"## {objective}\n\nStep-by-step guidance for {title}.",
                                exercise=f"Practise: {objective.lower()} with your own team.",
                                estimated_minutes=rng.choice([8, 12, 15, 20]),
                            )
                        )
            courses.append(course)
        await session.flush()

        # --- enrolments and progress -------------------------------------------------
        # Query rather than walk relationships: these Course rows were just constructed in
        # this session, so their collections are not loaded and touching them would emit a
        # lazy load (illegal on an async session).
        lesson_rows = (
            await session.execute(
                select(Module.course_id, Lesson.id)
                .join(Lesson, Lesson.module_id == Module.id)
                .where(Module.organization_id == org_id)
            )
        ).all()
        lesson_ids: dict[uuid.UUID, list[str]] = {course.id: [] for course in courses}
        for course_id, lesson_id in lesson_rows:
            lesson_ids.setdefault(course_id, []).append(str(lesson_id))
        for index, learner in enumerate(learners):
            course = courses[index % 2]
            enrollment = await session.scalar(
                select(Enrollment).where(
                    Enrollment.learner_id == learner.id, Enrollment.course_id == course.id
                )
            )
            if enrollment is not None:
                continue
            all_lessons = lesson_ids[course.id]
            done = all_lessons[: rng.randint(0, len(all_lessons))]
            progress = round(len(done) / len(all_lessons), 3) if all_lessons else 0.0
            session.add(
                Enrollment(
                    organization_id=org_id,
                    learner_id=learner.id,
                    course_id=course.id,
                    completed_lesson_ids=done,
                    progress=progress,
                    status="completed" if progress >= 1.0 else "active",
                    completed_at=now - timedelta(days=rng.randint(1, 10))
                    if progress >= 1.0
                    else None,
                )
            )
        await session.flush()

        # --- assessments and submissions ---------------------------------------------
        assessments: list[Assessment] = []
        for course in courses[:2]:
            assessment = await session.scalar(
                select(Assessment).where(Assessment.course_id == course.id)
            )
            if assessment is None:
                assessment = Assessment(
                    organization_id=org_id,
                    course_id=course.id,
                    title=f"{course.title} — final check",
                    description="Mixed multiple choice and written answers.",
                    passing_score=70,
                    difficulty=Difficulty.intermediate,
                    topics=["policy", "process"],
                    is_published=True,
                    model_used=settings.llm_model,
                    prompt_version=2,
                    validation_report={
                        "total": 3,
                        "accepted": [0, 1, 2],
                        "rejected": 0,
                        "issues": [],
                    },
                )
                session.add(assessment)
                await session.flush()
                session.add_all(
                    [
                        Question(
                            organization_id=org_id,
                            assessment_id=assessment.id,
                            position=0,
                            type=QuestionType.multiple_choice,
                            prompt="How many days per week may an employee work remotely?",
                            options=["One", "Two", "Three", "Five"],
                            correct_answer="Three",
                            explanation="The policy allows up to three remote days per week.",
                            difficulty=Difficulty.beginner,
                            learning_objective="Explain the remote policy",
                            source_reference=(
                                "Employees may work remotely up to three days per week."
                            ),
                            confidence=0.94,
                            points=1,
                        ),
                        Question(
                            organization_id=org_id,
                            assessment_id=assessment.id,
                            position=1,
                            type=QuestionType.true_false,
                            prompt="Internet costs are reimbursable under the remote work policy.",
                            options=["true", "false"],
                            correct_answer="false",
                            explanation="Internet costs are explicitly excluded.",
                            difficulty=Difficulty.beginner,
                            learning_objective="Request approval correctly",
                            source_reference=(
                                "Internet costs are not reimbursable under this policy."
                            ),
                            confidence=0.91,
                            points=1,
                        ),
                        Question(
                            organization_id=org_id,
                            assessment_id=assessment.id,
                            position=2,
                            type=QuestionType.short_answer,
                            prompt="Describe the approval process for a remote work request.",
                            options=[],
                            correct_answer=(
                                "Request approval from your direct manager at least five business "
                                "days in advance, quoting approval code RW-2291."
                            ),
                            explanation="Both the lead time and the approver must be named.",
                            rubric=(
                                "2 points: names the manager as approver AND the five business day "
                                "lead time. 1 point: names one. 0 points: neither."
                            ),
                            difficulty=Difficulty.intermediate,
                            learning_objective="Request approval correctly",
                            source_reference=(
                                "Requests must be approved by a direct manager at least five "
                                "business days in advance."
                            ),
                            confidence=0.88,
                            points=2,
                        ),
                    ]
                )
                await session.flush()
            assessments.append(assessment)

        for index, learner in enumerate(learners[:6]):
            assessment = assessments[index % len(assessments)]
            existing = await session.scalar(
                select(Submission).where(
                    Submission.learner_id == learner.id, Submission.assessment_id == assessment.id
                )
            )
            if existing is not None:
                continue
            questions = list(
                (
                    await session.execute(
                        select(Question)
                        .where(Question.assessment_id == assessment.id)
                        .order_by(Question.position)
                    )
                )
                .scalars()
                .all()
            )
            correct = index % 3 != 2
            submission = Submission(
                organization_id=org_id,
                assessment_id=assessment.id,
                learner_id=learner.id,
                submitted_at=now - timedelta(days=rng.randint(1, 12)),
                status="evaluated",
                answers=[
                    {
                        "question_id": str(q.id),
                        "answer": q.correct_answer
                        if correct
                        else "Not sure, something about a form.",
                    }
                    for q in questions
                ],
            )
            session.add(submission)
            await session.flush()

            score = float(sum(q.points for q in questions)) if correct else 1.0
            max_score = float(sum(q.points for q in questions))
            confidence = 0.92 if correct else 0.55
            needs_review = confidence < 0.75
            session.add(
                Evaluation(
                    organization_id=org_id,
                    submission_id=submission.id,
                    status=EvaluationStatus.needs_review
                    if needs_review
                    else EvaluationStatus.ai_completed,
                    score=score,
                    max_score=max_score,
                    confidence=confidence,
                    rationale="Answer covers the approver but omits the lead time."
                    if not correct
                    else "All required elements present.",
                    strengths=["Correct on the policy limit"] if correct else [],
                    missing_concepts=[] if correct else ["Five business day lead time"],
                    suggested_resources=["Remote Work Policy, section 1"],
                    per_question=[
                        {
                            "question_id": str(q.id),
                            "position": q.position,
                            "score": float(q.points) if correct else 0.0,
                            "max_score": float(q.points),
                            "confidence": 1.0
                            if q.type != QuestionType.short_answer
                            else confidence,
                            "graded_by": "rules" if q.type != QuestionType.short_answer else "ai",
                        }
                        for q in questions
                    ],
                    requires_human_review=needs_review,
                    model_used=settings.llm_model,
                    prompt_version=3,
                )
            )
            submission.auto_score = round(100 * score / max_score, 2)
        await session.flush()

        # --- workflows and executions -------------------------------------------------
        workflows: dict[str, Workflow] = {}
        for key, template in TEMPLATES.items():
            workflow = await session.scalar(
                select(Workflow).where(Workflow.organization_id == org_id, Workflow.key == key)
            )
            if workflow is None:
                workflow = Workflow(
                    organization_id=org_id,
                    key=key,
                    name=template["name"],
                    description=template["description"],
                    template=key,
                    trigger_type=TriggerType.schedule
                    if str(template["trigger_event"]).startswith("schedule")
                    else TriggerType.event,
                    trigger_event=template["trigger_event"],
                    config=template["config"],
                    n8n_webhook_path=f"learnflow/{key.replace('_', '-')}",
                )
                session.add(workflow)
                await session.flush()
            workflows[key] = workflow

        existing_runs = await session.scalar(
            select(WorkflowExecution.id).where(WorkflowExecution.organization_id == org_id).limit(1)
        )
        if existing_runs is None:
            for index in range(18):
                workflow = workflows[rng.choice(list(workflows))]
                failed = index % 7 == 3
                started = now - timedelta(hours=rng.randint(1, 240))
                duration = rng.randint(400, 9000)
                execution = WorkflowExecution(
                    organization_id=org_id,
                    workflow_id=workflow.id,
                    status=ExecutionStatus.failed if failed else ExecutionStatus.succeeded,
                    trigger_type=workflow.trigger_type,
                    triggered_by="event:seed",
                    idempotency_key=f"seed-{index}-{uuid.uuid4().hex[:8]}",
                    input_payload={"learner_id": str(rng.choice(learners).id)},
                    output_payload={} if failed else {"ok": True},
                    error="ProviderError: upstream timeout" if failed else None,
                    retry_count=1 if failed else 0,
                    n8n_execution_id=f"n8n-{2000 + index}",
                    started_at=started,
                    finished_at=started + timedelta(milliseconds=duration),
                    duration_ms=duration,
                    input_tokens=rng.randint(300, 2500),
                    output_tokens=rng.randint(80, 900),
                    estimated_cost_usd=round(rng.uniform(0.0005, 0.02), 6),
                    learner_id=rng.choice(learners).id,
                )
                session.add(execution)
                await session.flush()
                for position, (name, kind) in enumerate(
                    [
                        ("validate", "validation"),
                        ("generate_message", "ai"),
                        ("send_email", "email"),
                    ],
                    start=1,
                ):
                    step_failed = failed and position == 3
                    session.add(
                        WorkflowStep(
                            organization_id=org_id,
                            execution_id=execution.id,
                            position=position,
                            name=name,
                            kind=kind,
                            status="failed" if step_failed else "succeeded",
                            started_at=started,
                            finished_at=started + timedelta(milliseconds=duration // 3),
                            duration_ms=duration // 3,
                            output={} if step_failed else {"ok": True},
                            error="SMTP connection refused" if step_failed else None,
                        )
                    )

        # --- tickets -------------------------------------------------------------------
        for index, (subject, body, category, urgency, sentiment) in enumerate(TICKETS):
            existing = await session.scalar(
                select(SupportTicket).where(
                    SupportTicket.organization_id == org_id, SupportTicket.subject == subject
                )
            )
            if existing is not None:
                continue
            confidence = round(rng.uniform(0.45, 0.95), 2)
            session.add(
                SupportTicket(
                    organization_id=org_id,
                    subject=subject,
                    body=body,
                    learner_id=learners[index].id,
                    status="escalated" if confidence < 0.6 else "triaged",
                    category=category,
                    urgency=urgency,
                    sentiment=sentiment,
                    ai_confidence=confidence,
                    ai_explanation=f"Classified as {category} based on the reported symptom.",
                    suggested_response="Thanks for flagging this — here is what to try next.",
                    kb_references=[{"document": "support-playbook.md", "score": 0.71}],
                    escalated=confidence < 0.6,
                    assigned_to=users["support@learnflow.dev"].id,
                )
            )

        # --- historical AI usage ---------------------------------------------------------
        # Probe LLMCall, not UsageRecord: indexing the sample documents already wrote
        # embedding usage rows, so UsageRecord is never empty by this point.
        existing_usage = await session.scalar(
            select(LLMCall.id).where(LLMCall.organization_id == org_id).limit(1)
        )
        if existing_usage is None:
            operations = [
                "rag_answer",
                "course_generate",
                "assessment_generate",
                "assessment_evaluate",
                "ticket_triage",
                "workflow_welcome_message",
            ]
            for day in range(30):
                occurred = now - timedelta(days=day, hours=rng.randint(0, 20))
                for _ in range(rng.randint(2, 9)):
                    operation = rng.choice(operations)
                    model = rng.choice(["gpt-4o-mini", "gpt-4o"])
                    input_tokens = rng.randint(400, 6000)
                    output_tokens = rng.randint(80, 1400)
                    cost = estimate_cost(model, input_tokens, output_tokens)
                    success = rng.random() > 0.04
                    session.add(
                        LLMCall(
                            organization_id=org_id,
                            operation=operation,
                            provider="openai",
                            model=model,
                            prompt_name=operation,
                            prompt_version=2,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            latency_ms=rng.randint(280, 4200),
                            estimated_cost_usd=cost,
                            success=success,
                            retry_count=0 if success else 2,
                            error=None if success else "ProviderUnavailable: 503",
                            created_at=occurred,
                        )
                    )
                    if success:
                        session.add(
                            UsageRecord(
                                organization_id=org_id,
                                kind="llm",
                                provider="openai",
                                model=model,
                                operation=operation,
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                estimated_cost_usd=cost,
                                occurred_at=occurred,
                            )
                        )

        log.info("seed_complete", organization=ORG_SLUG, learners=len(learners))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed LearnFlow demo data")
    parser.add_argument("--reset", action="store_true", help="Delete the demo org first")
    args = parser.parse_args()
    configure_logging(json_output=False)
    asyncio.run(seed(reset=args.reset))
    print(f"\nDemo organization ready.\n  admin@learnflow.dev / {DEMO_PASSWORD}\n")


if __name__ == "__main__":
    main()
