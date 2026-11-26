"""Course curriculum management, AI generation, editing, and publishing API router.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file manages courses, modules, and lessons:
1. `GET /courses`: Lists courses. If the caller is a `Learner`, results are strictly filtered
   to published courses where the learner holds an active enrollment.
2. `POST /courses/generate`: Enqueues an async background job (`generate_course`) to create
   a complete multi-module curriculum grounded in uploaded documents.
3. `GET /courses/{id}`: Returns course details with eagerly-loaded modules and lessons
(`selectinload`).
4. `PATCH /courses/{id}` & `/lessons/{id}`: Enables manual instructor edits to titles and lesson
markdown.
5. `POST /courses/{id}/modules/{id}/regenerate`: Asynchronously rewrites a single module with AI.
6. `POST /courses/{id}/publish`: Transitions course from `draft`/`review` to `published`.

===============================================================================
MULTI-TENANCY & LEARNER SCOPING:
===============================================================================
Instructors and admins have full visibility across all draft/review/published courses in the tenant.
Learners are restricted to `status == 'published'` AND `Enrollment.learner_id ==
current_learner_id`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..deps import PrincipalDep, RepoDep, SessionDep, require, scoped_learner_id
from ..errors import Conflict, NotFound
from ..models import Course, CourseStatus, Enrollment, KnowledgeBase, Lesson, Module
from ..schemas.common import Page, PageParams, page_params
from ..schemas.learning import (
    CourseCreate,
    CourseDetail,
    CourseGenerateRequest,
    CourseOut,
    CourseUpdate,
    LessonOut,
    LessonUpdate,
    ModuleOut,
    RegenerateModuleRequest,
)
from ..services import audit, jobs
from ..services.generation import slugify

router = APIRouter(prefix="/courses", tags=["courses"])


async def _detail(session, repo, course: Course) -> CourseDetail:
    """Helper hydrating course detail with eagerly loaded modules, lessons, and enrollment
    counts.
    """
    modules = (
        (
            await session.execute(
                select(Module)
                .where(
                    Module.course_id == course.id, Module.organization_id == repo.organization_id
                )
                .options(selectinload(Module.lessons))
                .order_by(Module.position)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    detail = CourseDetail(**CourseOut.model_validate(course).model_dump())
    detail.modules = [
        ModuleOut(
            id=m.id,
            title=m.title,
            position=m.position,
            summary=m.summary,
            learning_objectives=m.learning_objectives,
            lessons=[
                LessonOut.model_validate(x) for x in sorted(m.lessons, key=lambda x: x.position)
            ],
        )
        for m in modules
    ]
    detail.enrollment_count = int(
        await session.scalar(
            select(func.count()).select_from(Enrollment).where(Enrollment.course_id == course.id)
        )
        or 0
    )
    return detail


@router.get("", response_model=Page[CourseOut], summary="List courses")
async def list_courses(
    repo: RepoDep,
    principal: PrincipalDep,
    params: PageParams = Depends(page_params),
    course_status: CourseStatus | None = Query(None, alias="status"),
) -> Page[CourseOut]:
    """Lists courses in the tenant, scoped by role (learners only see their enrolled published
    courses).
    """
    where = [Course.status == course_status] if course_status else []
    if principal.is_learner:
        where.append(Course.status == CourseStatus.published)
        where.append(
            Course.id.in_(
                select(Enrollment.course_id).where(
                    Enrollment.learner_id == scoped_learner_id(principal, None)
                )
            )
        )
    items, total = await repo.list(
        Course, where=where, sort=params.sort, limit=params.limit, offset=params.offset
    )
    return Page(
        items=[CourseOut.model_validate(i) for i in items],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.post(
    "",
    response_model=CourseDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require("course:write"))],
    summary="Create an empty course by hand",
)
async def create_course(body: CourseCreate, repo: RepoDep, session: SessionDep) -> CourseDetail:
    """Manually creates an empty course container."""
    course = repo.add(
        Course(
            title=body.title,
            slug=slugify(body.title),
            description=body.description,
            workspace_id=body.workspace_id,
        )
    )
    await repo.flush()
    return await _detail(session, repo, course)


@router.post(
    "/generate",
    response_model=CourseDetail,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require("course:write"))],
    summary="Generate a course from indexed documents (async)",
)
async def generate(
    body: CourseGenerateRequest,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> CourseDetail:
    """Dispatches an asynchronous AI course generation job grounded in knowledge base documents."""
    await repo.get(KnowledgeBase, body.knowledge_base_id)
    course = repo.add(
        Course(
            title=f"Generating: {body.subject}",
            slug=slugify(body.subject),
            status=CourseStatus.generating,
            knowledge_base_id=body.knowledge_base_id,
            source_document_ids=[str(d) for d in body.document_ids],
            workspace_id=body.workspace_id,
            created_by=principal.user.id if principal.user else None,
        )
    )
    await repo.flush()
    job = await jobs.enqueue(
        session,
        repo.organization_id,
        "generate_course",
        idempotency_key=f"generate_course:{course.id}",
        resource_type="course",
        resource_id=str(course.id),
        course_id=str(course.id),
        subject=body.subject,
        module_count=body.module_count,
        audience=body.audience,
    )
    course.generation_job_id = str(job.id)
    await repo.flush()
    await audit.record(
        session, principal, "course.generate", "course", str(course.id), request=request
    )
    detail = await _detail(session, repo, course)
    detail.job_id = job.id
    return detail


@router.get("/{course_id}", response_model=CourseDetail, summary="Course with modules and lessons")
async def get_course(
    course_id: uuid.UUID, repo: RepoDep, session: SessionDep, principal: PrincipalDep
) -> CourseDetail:
    """Fetches full course details including modules and lesson content."""
    course = await repo.get(Course, course_id)
    if principal.is_learner:
        enrolled = await session.scalar(
            repo.select(Enrollment).where(
                Enrollment.course_id == course.id,
                Enrollment.learner_id == scoped_learner_id(principal, None),
            )
        )
        if enrolled is None or course.status is not CourseStatus.published:
            raise NotFound("Course not found")
    return await _detail(session, repo, course)


@router.patch(
    "/{course_id}",
    response_model=CourseDetail,
    dependencies=[Depends(require("course:write"))],
    summary="Edit course metadata",
)
async def update_course(
    course_id: uuid.UUID, body: CourseUpdate, repo: RepoDep, session: SessionDep
) -> CourseDetail:
    """Updates course title, description, or audience."""
    course = await repo.get(Course, course_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(course, field, value)
    if body.title:
        course.slug = slugify(body.title)
    await repo.flush()
    return await _detail(session, repo, course)


@router.post(
    "/{course_id}/publish",
    response_model=CourseDetail,
    dependencies=[Depends(require("course:publish"))],
    summary="Publish a reviewed course",
)
async def publish(
    course_id: uuid.UUID,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> CourseDetail:
    """Publishes a course making it accessible to enrolled learners."""
    course = await repo.get(Course, course_id)
    module_count = int(
        await session.scalar(
            select(func.count()).select_from(Module).where(Module.course_id == course.id)
        )
        or 0
    )
    if module_count == 0:
        raise Conflict("Cannot publish a course with no modules")
    if course.status == CourseStatus.generating:
        raise Conflict("Course is still being generated")
    course.status = CourseStatus.published
    course.published_at = datetime.now(UTC)
    await repo.flush()
    await audit.record(
        session, principal, "course.publish", "course", str(course.id), request=request
    )
    return await _detail(session, repo, course)


@router.post(
    "/{course_id}/modules/{module_id}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require("course:write"))],
    summary="Regenerate a single module (async)",
)
async def regenerate_module(
    course_id: uuid.UUID,
    module_id: uuid.UUID,
    body: RegenerateModuleRequest,
    repo: RepoDep,
    session: SessionDep,
) -> dict[str, str]:
    """Enqueues an async task to rewrite a single module with AI."""
    course = await repo.get(Course, course_id)
    module = await repo.get(Module, module_id)
    if module.course_id != course.id:
        raise NotFound("Module not found on this course")
    job = await jobs.enqueue(
        session,
        repo.organization_id,
        "regenerate_module",
        idempotency_key=f"regen_module:{module.id}:{uuid.uuid4().hex[:8]}",
        resource_type="module",
        resource_id=str(module.id),
        course_id=str(course.id),
        module_id=str(module.id),
        instructions=body.instructions,
    )
    return {"job_id": str(job.id), "status": job.status}


@router.patch(
    "/{course_id}/lessons/{lesson_id}",
    response_model=LessonOut,
    dependencies=[Depends(require("course:write"))],
    summary="Edit generated lesson content",
)
async def update_lesson(
    course_id: uuid.UUID, lesson_id: uuid.UUID, body: LessonUpdate, repo: RepoDep
) -> Lesson:
    """Updates lesson content, exercises, or title."""
    await repo.get(Course, course_id)
    lesson = await repo.get(Lesson, lesson_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(lesson, field, value)
    await repo.flush()
    return lesson


@router.delete(
    "/{course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require("course:write"))],
    summary="Delete a course",
)
async def delete_course(
    course_id: uuid.UUID,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> None:
    """Deletes a course, cascading to all associated modules and lessons."""
    course = await repo.get(Course, course_id)
    await repo.delete(course)
    await audit.record(
        session, principal, "course.delete", "course", str(course_id), request=request
    )
