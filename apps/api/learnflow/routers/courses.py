"""Course generation, editing, review and publishing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..deps import PrincipalDep, RepoDep, SessionDep, require
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
    params: PageParams = Depends(page_params),
    course_status: CourseStatus | None = Query(None, alias="status"),
) -> Page[CourseOut]:
    where = [Course.status == course_status] if course_status else []
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
async def get_course(course_id: uuid.UUID, repo: RepoDep, session: SessionDep) -> CourseDetail:
    return await _detail(session, repo, await repo.get(Course, course_id))


@router.patch(
    "/{course_id}",
    response_model=CourseDetail,
    dependencies=[Depends(require("course:write"))],
    summary="Edit course metadata",
)
async def update_course(
    course_id: uuid.UUID, body: CourseUpdate, repo: RepoDep, session: SessionDep
) -> CourseDetail:
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
    course = await repo.get(Course, course_id)
    await repo.delete(course)
    await audit.record(
        session, principal, "course.delete", "course", str(course_id), request=request
    )
