"""Learner directory, course enrollment, progress tracking, and portal access router.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file manages student records and their learning progress:
1. `POST /learners`: Creates a learner record and automatically emits the `learner.created`
   event, triggering the onboarding automation workflow.
2. `GET /learners`: Lists students in the organization (if caller is a student, scoped to self).
3. `POST /learners/{id}/progress`: Marks individual lessons complete, recalculates percentage
   progress, and emits `course.completed` when progress reaches 100%.
4. `POST /learners/enroll`: Bulk enrolls multiple students into a course.
5. `POST /learners/{id}/access`: Provisions a student login account with `Role.learner`,
   linking their `Learner` record to a `User` entity for portal authentication.

===============================================================================
SEPARATION OF CONCERNS (Learner vs User):
===============================================================================
In enterprise LMS platforms, thousands of employees are synchronized from an HRIS database
into `Learner` rows before they ever log in. `grant_access` is the explicit gateway that
promotes a passive `Learner` into an authenticated `User`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select

from ..automation import events
from ..deps import PrincipalDep, RepoDep, SessionDep, require, scoped_learner_id
from ..errors import Conflict, NotFound, ValidationFailed
from ..models import Course, Enrollment, Learner, Lesson, Module, OrganizationMember, Role, User
from ..schemas.common import Page, PageParams, page_params
from ..schemas.learning import EnrollRequest, ProgressUpdate
from ..schemas.tenancy import (
    EnrollmentOut,
    LearnerAccessGrant,
    LearnerAccessResult,
    LearnerCreate,
    LearnerDetail,
    LearnerOut,
    LearnerUpdate,
)
from ..security import hash_password
from ..services import audit

router = APIRouter(prefix="/learners", tags=["learners"])


async def _enrollments(session, repo, learner: Learner) -> list[EnrollmentOut]:
    """Helper returning all active course enrollments and progress for a learner."""
    rows = (
        await session.execute(
            select(Enrollment, Course.title)
            .join(Course, Course.id == Enrollment.course_id)
            .where(
                Enrollment.learner_id == learner.id,
                Enrollment.organization_id == repo.organization_id,
            )
        )
    ).all()
    return [
        EnrollmentOut(
            id=e.id,
            course_id=e.course_id,
            course_title=title,
            status=e.status,
            progress=e.progress,
            completed_lesson_ids=e.completed_lesson_ids,
            completed_at=e.completed_at,
            created_at=e.created_at,
        )
        for e, title in rows
    ]


@router.get("", response_model=Page[LearnerOut], summary="List learners")
async def list_learners(
    repo: RepoDep,
    principal: PrincipalDep,
    params: PageParams = Depends(page_params),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None, min_length=2),
) -> Page[LearnerOut]:
    """Lists learners in the tenant, with full-text name/email search."""
    where = []
    if principal.is_learner:
        where.append(Learner.id == scoped_learner_id(principal, None))
    if status_filter:
        where.append(Learner.status == status_filter)
    if search:
        where.append(Learner.full_name.ilike(f"%{search}%") | Learner.email.ilike(f"%{search}%"))
    items, total = await repo.list(
        Learner, where=where, sort=params.sort, limit=params.limit, offset=params.offset
    )
    return Page(
        items=[LearnerOut.model_validate(i) for i in items],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.post(
    "",
    response_model=LearnerDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require("learner:manage"))],
    summary="Add a learner (fires the onboarding workflow)",
)
async def create_learner(
    body: LearnerCreate,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> LearnerDetail:
    """Creates a learner record and dispatches the `learner.created` domain event."""
    duplicate = await session.scalar(
        repo.select(Learner).where(Learner.email == body.email.lower())
    )
    if duplicate is not None:
        raise Conflict("A learner with that email already exists in this organization")

    learner = repo.add(
        Learner(
            email=body.email.lower(),
            full_name=body.full_name,
            workspace_id=body.workspace_id,
            external_ref=body.external_ref,
            profile=body.profile,
        )
    )
    await repo.flush()

    if body.course_id is not None:
        course = await repo.get(Course, body.course_id)
        repo.add(Enrollment(learner_id=learner.id, course_id=course.id))
        await repo.flush()

    await audit.record(
        session,
        principal,
        "learner.create",
        "learner",
        str(learner.id),
        request=request,
        details={"email": learner.email},
    )
    # Dispatch domain event triggering background welcome message and onboarding email
    await events.emit(
        session,
        repo.organization_id,
        "learner.created",
        {
            "learner_id": str(learner.id),
            "course_id": str(body.course_id) if body.course_id else None,
        },
        learner_id=learner.id,
        course_id=body.course_id,
        idempotency_key=str(learner.id),
    )
    detail = LearnerDetail.model_validate(learner)
    detail.enrollments = await _enrollments(session, repo, learner)
    return detail


@router.get("/{learner_id}", response_model=LearnerDetail, summary="Learner detail")
async def get_learner(
    learner_id: uuid.UUID, repo: RepoDep, session: SessionDep, principal: PrincipalDep
) -> LearnerDetail:
    """Returns learner profile, active enrollments, and progress."""
    scoped_learner_id(principal, learner_id)
    learner = await repo.get(Learner, learner_id)
    detail = LearnerDetail.model_validate(learner)
    detail.enrollments = await _enrollments(session, repo, learner)
    return detail


@router.patch(
    "/{learner_id}",
    response_model=LearnerOut,
    dependencies=[Depends(require("learner:manage"))],
    summary="Update a learner",
)
async def update_learner(learner_id: uuid.UUID, body: LearnerUpdate, repo: RepoDep) -> Learner:
    """Updates learner profile information or external HRIS references."""
    learner = await repo.get(Learner, learner_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(learner, field, value)
    await repo.flush()
    return learner


@router.delete(
    "/{learner_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require("learner:manage"))],
    summary="Delete a learner",
)
async def delete_learner(
    learner_id: uuid.UUID,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> None:
    """Deletes a learner and cascades to their submissions and enrollments."""
    learner = await repo.get(Learner, learner_id)
    await repo.delete(learner)
    await audit.record(
        session, principal, "learner.delete", "learner", str(learner_id), request=request
    )


@router.post(
    "/{learner_id}/activity",
    response_model=LearnerOut,
    summary="Record learner activity (keeps them out of the inactivity workflow)",
)
async def touch_activity(learner_id: uuid.UUID, repo: RepoDep, principal: PrincipalDep) -> Learner:
    """Updates last active timestamp and resets inactivity reminder count."""
    scoped_learner_id(principal, learner_id)
    learner = await repo.get(Learner, learner_id)
    learner.last_active_at = datetime.now(UTC)
    learner.reminders_sent = 0
    await repo.flush()
    return learner


@router.post(
    "/{learner_id}/progress",
    response_model=EnrollmentOut,
    summary="Mark a lesson complete and recompute course progress",
)
async def update_progress(
    learner_id: uuid.UUID,
    course_id: uuid.UUID,
    body: ProgressUpdate,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
) -> EnrollmentOut:
    """Records completion of a lesson, recalculates overall percentage, and fires course completion events."""
    scoped_learner_id(principal, learner_id)
    learner = await repo.get(Learner, learner_id)
    enrollment = await session.scalar(
        repo.select(Enrollment).where(
            Enrollment.learner_id == learner.id, Enrollment.course_id == course_id
        )
    )
    if enrollment is None:
        raise NotFound("Learner is not enrolled in that course")

    completed = {str(x) for x in enrollment.completed_lesson_ids}
    if body.completed:
        completed.add(str(body.lesson_id))
    else:
        completed.discard(str(body.lesson_id))
    enrollment.completed_lesson_ids = sorted(completed)

    total_lessons = len(
        (
            await session.execute(
                select(Lesson.id)
                .join(Module, Module.id == Lesson.module_id)
                .where(
                    Module.course_id == course_id, Lesson.organization_id == repo.organization_id
                )
            )
        )
        .scalars()
        .all()
    )
    enrollment.progress = round(len(completed) / total_lessons, 3) if total_lessons else 0.0
    learner.last_active_at = datetime.now(UTC)

    # If completed all lessons, emit course.completed event
    if enrollment.progress >= 1.0 and enrollment.completed_at is None:
        enrollment.completed_at = datetime.now(UTC)
        enrollment.status = "completed"
        await events.emit(
            session,
            repo.organization_id,
            "course.completed",
            {"learner_id": str(learner.id), "course_id": str(course_id)},
            learner_id=learner.id,
            course_id=course_id,
            idempotency_key=f"{learner.id}:{course_id}",
        )
    await repo.flush()
    course = await repo.get(Course, course_id)
    return EnrollmentOut(
        id=enrollment.id,
        course_id=course_id,
        course_title=course.title,
        status=enrollment.status,
        progress=enrollment.progress,
        completed_lesson_ids=enrollment.completed_lesson_ids,
        completed_at=enrollment.completed_at,
        created_at=enrollment.created_at,
    )


@router.post(
    "/enroll",
    response_model=list[EnrollmentOut],
    dependencies=[Depends(require("learner:manage"))],
    summary="Bulk-enrol learners into a course",
)
async def bulk_enroll(
    course_id: uuid.UUID, body: EnrollRequest, repo: RepoDep, session: SessionDep
) -> list[EnrollmentOut]:
    """Enrolls multiple learners into a course."""
    course = await repo.get(Course, course_id)
    created: list[EnrollmentOut] = []
    for learner_id in body.learner_ids:
        learner = await repo.get(Learner, learner_id)
        existing = await session.scalar(
            repo.select(Enrollment).where(
                Enrollment.learner_id == learner.id, Enrollment.course_id == course.id
            )
        )
        enrollment = existing or repo.add(Enrollment(learner_id=learner.id, course_id=course.id))
        await repo.flush()
        created.append(
            EnrollmentOut(
                id=enrollment.id,
                course_id=course.id,
                course_title=course.title,
                status=enrollment.status,
                progress=enrollment.progress,
                completed_lesson_ids=enrollment.completed_lesson_ids,
                completed_at=enrollment.completed_at,
                created_at=enrollment.created_at,
            )
        )
    return created


@router.post(
    "/{learner_id}/access",
    response_model=LearnerAccessResult,
    dependencies=[Depends(require("learner:manage"))],
    summary="Give a learner a login for the learner portal",
)
async def grant_access(
    learner_id: uuid.UUID,
    body: LearnerAccessGrant,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> LearnerAccessResult:
    """Provisions a User login account with `Role.learner` linked to this student record."""
    learner = await repo.get(Learner, learner_id)

    user = await session.scalar(select(User).where(User.email == learner.email))
    created = user is None
    if user is None:
        if not body.password:
            raise ValidationFailed(
                "A new account needs an initial password (there is no mail server configured)"
            )
        user = User(
            email=learner.email,
            full_name=learner.full_name,
            password_hash=hash_password(body.password),
        )
        session.add(user)
        await session.flush()
    elif body.password:
        user.password_hash = hash_password(body.password)

    membership = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == repo.organization_id,
            OrganizationMember.user_id == user.id,
        )
    )
    if membership is None:
        session.add(
            OrganizationMember(
                organization_id=repo.organization_id, user_id=user.id, role=Role.learner
            )
        )
    elif membership.role is not Role.learner:
        raise Conflict(
            f"That email already belongs to a {membership.role.value} in this organization"
        )

    learner.user_id = user.id
    await repo.flush()
    await audit.record(
        session,
        principal,
        "learner.grant_access",
        "learner",
        str(learner.id),
        request=request,
        details={"email": learner.email, "account_created": created},
    )
    return LearnerAccessResult(
        learner_id=learner.id, user_id=user.id, email=learner.email, account_created=created
    )
