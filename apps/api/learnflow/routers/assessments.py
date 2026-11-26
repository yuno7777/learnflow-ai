"""Assessment generation, student submissions, and AI evaluation review API router.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file manages quizzes, test submissions, and instructor review workflows:
1. `POST /assessments/generate`: Enqueues an async AI quiz generation job (`generate_assessment`).
2. `GET /assessments/{id}`: Returns assessment questions.
   - Answer Key Redaction: `_serialize_question` strips `correct_answer`, `rubric`,
     `explanation`, and `confidence` unless the caller has `assessment:write` permission
     and explicitly passes `?include_answers=true`.
3. `POST /assessments/{id}/submissions`: Submits student answers, validates question IDs,
   and enqueues the `evaluate_submission` grading job.
4. `GET /submissions/{id}`: Returns submission details with score breakdown and feedback.
5. `GET /evaluations/queue`: Lists pending low-confidence evaluations awaiting instructor review.
6. `POST /evaluations/{id}/review`: Human instructor sign-off:
   - Security Gate: Rejects machine API keys (`principal.user is None`) to ensure only
     verified human staff can approve or override grades.

===============================================================================
HOW IT CONNECTS TO THE REST OF THE PROJECT:
===============================================================================
- `services/generation.py`: Generates quiz questions and runs validation filters.
- `services/evaluation.py`: Auto-grades objective questions and runs LLM rubric scoring.
- `worker/tasks.py`: Executes grading as background task.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, status

from ..deps import PrincipalDep, RepoDep, SessionDep, require, scoped_learner_id
from ..errors import Conflict, Forbidden, NotFound, ValidationFailed
from ..models import (
    Assessment,
    Evaluation,
    EvaluationStatus,
    KnowledgeBase,
    Learner,
    Question,
    Submission,
)
from ..schemas.common import Page, PageParams, page_params
from ..schemas.learning import (
    AssessmentDetail,
    AssessmentGenerateRequest,
    AssessmentOut,
    EvaluationOut,
    EvaluationReview,
    QuestionOut,
    SubmissionCreate,
    SubmissionDetail,
    SubmissionOut,
)
from ..services import audit, jobs
from ..services.evaluation import review_evaluation

router = APIRouter(prefix="/assessments", tags=["assessments"])
submissions_router = APIRouter(prefix="/submissions", tags=["assessments"])
evaluations_router = APIRouter(prefix="/evaluations", tags=["assessments"])

ANSWER_KEY_FIELDS = ("correct_answer", "explanation", "source_reference", "confidence", "rubric")


def _serialize_question(question: Question, *, include_answers: bool) -> QuestionOut:
    """Serializes a Question, redacting answer keys if caller is not authorized."""
    data = QuestionOut.model_validate(question)
    if not include_answers:
        for field in ANSWER_KEY_FIELDS:
            setattr(data, field, None)
    return data


# -----------------------------------------------------------------------------
# Assessment Endpoints
# -----------------------------------------------------------------------------


@router.get("", response_model=Page[AssessmentOut], summary="List assessments")
async def list_assessments(
    repo: RepoDep,
    principal: PrincipalDep,
    params: PageParams = Depends(page_params),
    course_id: uuid.UUID | None = Query(None),
) -> Page[AssessmentOut]:
    """Lists assessments, filtering unpublished items for student accounts."""
    where = [Assessment.course_id == course_id] if course_id else []
    if principal.is_learner:
        where.append(Assessment.is_published.is_(True))
    items, total = await repo.list(
        Assessment, where=where, sort=params.sort, limit=params.limit, offset=params.offset
    )
    return Page(
        items=[AssessmentOut.model_validate(i) for i in items],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.post(
    "/generate",
    response_model=AssessmentDetail,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require("assessment:write"))],
    summary="Generate questions from indexed documents (async)",
)
async def generate(
    body: AssessmentGenerateRequest,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> AssessmentDetail:
    """Enqueues async AI quiz question generation grounded in knowledge base documents."""
    await repo.get(KnowledgeBase, body.knowledge_base_id)
    assessment = repo.add(
        Assessment(
            course_id=body.course_id,
            title=body.title,
            difficulty=body.difficulty,
            topics=body.topics,
            passing_score=body.passing_score,
        )
    )
    await repo.flush()
    job = await jobs.enqueue(
        session,
        repo.organization_id,
        "generate_assessment",
        idempotency_key=f"generate_assessment:{assessment.id}",
        resource_type="assessment",
        resource_id=str(assessment.id),
        assessment_id=str(assessment.id),
        document_ids=[str(d) for d in body.document_ids],
        question_count=body.question_count,
        question_types=[t.value for t in body.question_types],
        topics=body.topics,
    )
    assessment.generation_job_id = str(job.id)
    await repo.flush()
    await audit.record(
        session, principal, "assessment.generate", "assessment", str(assessment.id), request=request
    )
    return AssessmentDetail(**AssessmentOut.model_validate(assessment).model_dump(), job_id=job.id)


@router.get(
    "/{assessment_id}", response_model=AssessmentDetail, summary="Assessment with questions"
)
async def get_assessment(
    assessment_id: uuid.UUID,
    repo: RepoDep,
    principal: PrincipalDep,
    include_answers: bool = Query(False, description="Answer key — instructors and reviewers only"),
) -> AssessmentDetail:
    """Fetches assessment questions, redacting correct answers unless authorized."""
    assessment = await repo.get(Assessment, assessment_id)
    if principal.is_learner and not assessment.is_published:
        raise NotFound("Assessment not found")
    if include_answers and not principal.has("assessment:write"):
        raise Forbidden("Your role cannot view the answer key")
    detail = AssessmentDetail(**AssessmentOut.model_validate(assessment).model_dump())
    detail.questions = [
        _serialize_question(q, include_answers=include_answers) for q in assessment.questions
    ]
    return detail


@router.post(
    "/{assessment_id}/publish",
    response_model=AssessmentOut,
    dependencies=[Depends(require("assessment:write"))],
    summary="Publish an assessment to learners",
)
async def publish(assessment_id: uuid.UUID, repo: RepoDep) -> Assessment:
    """Publishes an assessment quiz so students can take it."""
    assessment = await repo.get(Assessment, assessment_id)
    if not assessment.questions:
        raise Conflict("Cannot publish an assessment with no questions")
    assessment.is_published = True
    await repo.flush()
    return assessment


# -----------------------------------------------------------------------------
# Submission Endpoints
# -----------------------------------------------------------------------------


@router.post(
    "/{assessment_id}/submissions",
    response_model=SubmissionDetail,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit answers and queue AI evaluation",
)
async def submit(
    assessment_id: uuid.UUID,
    body: SubmissionCreate,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> SubmissionDetail:
    """Submits student answers and dispatches async background grading job."""
    assessment = await repo.get(Assessment, assessment_id)
    if not assessment.is_published:
        raise Conflict("Assessment is not published")
    target = scoped_learner_id(principal, body.learner_id)
    if target is None:
        raise ValidationFailed("A submission must name a learner")
    learner = await repo.get(Learner, target)

    valid_ids = {q.id for q in assessment.questions}
    unknown = [str(a.question_id) for a in body.answers if a.question_id not in valid_ids]
    if unknown:
        raise NotFound("Some answers reference questions not on this assessment", {"ids": unknown})

    submission = repo.add(
        Submission(
            assessment_id=assessment.id,
            learner_id=learner.id,
            answers=[{"question_id": str(a.question_id), "answer": a.answer} for a in body.answers],
            submitted_at=datetime.now(UTC),
        )
    )
    learner.last_active_at = datetime.now(UTC)
    await repo.flush()

    job = await jobs.enqueue(
        session,
        repo.organization_id,
        "evaluate_submission",
        idempotency_key=f"evaluate:{submission.id}",
        resource_type="submission",
        resource_id=str(submission.id),
        submission_id=str(submission.id),
    )
    await audit.record(
        session, principal, "submission.create", "submission", str(submission.id), request=request
    )
    return SubmissionDetail(**SubmissionOut.model_validate(submission).model_dump(), job_id=job.id)


@submissions_router.get("", response_model=Page[SubmissionOut], summary="List submissions")
async def list_submissions(
    repo: RepoDep,
    principal: PrincipalDep,
    params: PageParams = Depends(page_params),
    assessment_id: uuid.UUID | None = Query(None),
    learner_id: uuid.UUID | None = Query(None),
    submission_status: str | None = Query(None, alias="status"),
) -> Page[SubmissionOut]:
    """Lists assessment submissions, scoped by role."""
    where = []
    learner_id = scoped_learner_id(principal, learner_id)
    if assessment_id:
        where.append(Submission.assessment_id == assessment_id)
    if learner_id:
        where.append(Submission.learner_id == learner_id)
    if submission_status:
        where.append(Submission.status == submission_status)
    items, total = await repo.list(
        Submission, where=where, sort=params.sort, limit=params.limit, offset=params.offset
    )
    return Page(
        items=[SubmissionOut.model_validate(i) for i in items],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@submissions_router.get(
    "/{submission_id}", response_model=SubmissionDetail, summary="Submission with its evaluation"
)
async def get_submission(
    submission_id: uuid.UUID, repo: RepoDep, principal: PrincipalDep
) -> SubmissionDetail:
    """Fetches a submission and its auto-graded evaluation breakdown."""
    submission = await repo.get(Submission, submission_id)
    scoped_learner_id(principal, submission.learner_id)
    detail = SubmissionDetail(**SubmissionOut.model_validate(submission).model_dump())
    if submission.evaluation is not None:
        detail.evaluation = EvaluationOut.model_validate(submission.evaluation)
    return detail


# -----------------------------------------------------------------------------
# Evaluation & HITL Review Endpoints
# -----------------------------------------------------------------------------


@evaluations_router.get(
    "", response_model=Page[EvaluationOut], summary="List evaluations (filter for the review queue)"
)
async def list_evaluations(
    repo: RepoDep,
    params: PageParams = Depends(page_params),
    needs_review: bool | None = Query(None),
) -> Page[EvaluationOut]:
    """Lists evaluation records, with optional filtering for items requiring human review."""
    where = []
    if needs_review is not None:
        where.append(Evaluation.requires_human_review.is_(needs_review))
    items, total = await repo.list(
        Evaluation, where=where, sort=params.sort, limit=params.limit, offset=params.offset
    )
    return Page(
        items=[EvaluationOut.model_validate(i) for i in items],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@evaluations_router.post(
    "/{evaluation_id}/review",
    response_model=EvaluationOut,
    dependencies=[Depends(require("evaluation:approve"))],
    summary="Human approval or rejection of an AI evaluation",
)
async def review(
    evaluation_id: uuid.UUID,
    body: EvaluationReview,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> Evaluation:
    """Applies human instructor approval or manual score adjustment to an AI evaluation."""
    evaluation = await repo.get(Evaluation, evaluation_id)
    if evaluation.status in (EvaluationStatus.pending, EvaluationStatus.failed):
        raise Conflict("Evaluation has not produced a result yet")
    if principal.user is None:
        raise Forbidden("API keys cannot approve evaluations — a human must sign off")

    result = await review_evaluation(
        session,
        evaluation,
        principal.user.id,
        approve=body.approve,
        score=body.score,
        notes=body.notes,
    )
    await audit.record(
        session,
        principal,
        "evaluation.review",
        "evaluation",
        str(evaluation.id),
        request=request,
        details={"approved": body.approve, "score": result.score},
    )
    return result


@evaluations_router.get(
    "/queue", response_model=list[EvaluationOut], summary="Evaluations awaiting human review"
)
async def review_queue(repo: RepoDep, session: SessionDep) -> list[Evaluation]:
    """Returns the queue of low-confidence evaluations awaiting instructor review."""
    rows = (
        (
            await session.execute(
                repo.select(Evaluation)
                .where(Evaluation.status == EvaluationStatus.needs_review)
                .order_by(Evaluation.created_at)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)
