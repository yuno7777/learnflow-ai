"""Course and assessment generation.

Both take source documents, pull representative chunks, ask the model for a strictly-typed
structure, validate it, and persist it. Validation is not just schema shape: generated quiz
items are additionally checked for duplicates, ambiguity and missing rubrics before they are
allowed to reach a learner.
"""

from __future__ import annotations

import re
import uuid
from difflib import SequenceMatcher
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.base import ChatMessage
from ..ai.gateway import AIGateway
from ..ai.prompts import COURSE_OUTLINE, QUIZ_GENERATE
from ..ai.structured import GeneratedCourse, GeneratedQuiz
from ..errors import NotFound, ValidationFailed
from ..logging import get_logger
from ..models import (
    Assessment,
    Course,
    CourseStatus,
    Difficulty,
    DocumentChunk,
    Lesson,
    Module,
    Question,
    QuestionType,
)

log = get_logger(__name__)

MAX_CONTEXT_CHUNKS = 40
DUPLICATE_THRESHOLD = 0.85
AMBIGUOUS_MARKERS = ("all of the above", "none of the above", "both a and b", "not sure")


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:110] or "course"


async def _source_context(
    session: AsyncSession,
    organization_id: uuid.UUID,
    document_ids: list[uuid.UUID],
    limit: int = MAX_CONTEXT_CHUNKS,
) -> str:
    """Evenly sample chunks across the selected documents so no single file dominates."""
    per_doc = max(1, limit // max(1, len(document_ids)))
    parts: list[str] = []
    for document_id in document_ids:
        rows = (
            (
                await session.execute(
                    select(DocumentChunk)
                    .where(
                        DocumentChunk.organization_id == organization_id,
                        DocumentChunk.document_id == document_id,
                    )
                    .order_by(DocumentChunk.position)
                    .limit(per_doc * 3)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            continue
        step = max(1, len(rows) // per_doc)
        parts.extend(chunk.content for chunk in rows[::step][:per_doc])
    if not parts:
        raise ValidationFailed(
            "Selected documents have no indexed chunks yet — wait for ingestion to finish"
        )
    return "\n\n---\n\n".join(parts)


# --------------------------------------------------------------------------- course
async def generate_course(
    session: AsyncSession,
    gateway: AIGateway,
    course: Course,
    *,
    subject: str,
    module_count: int,
    audience: str,
) -> Course:
    context = await _source_context(
        session, course.organization_id, [uuid.UUID(str(d)) for d in course.source_document_ids]
    )
    system, user = COURSE_OUTLINE.render(
        context=context[:24000], subject=subject, module_count=str(module_count), audience=audience
    )
    result = await gateway.complete(
        "course_generate",
        [ChatMessage("system", system), ChatMessage("user", user)],
        prompt=COURSE_OUTLINE,
        schema=GeneratedCourse,
        temperature=0.3,
        max_tokens=6000,
    )
    generated = cast(GeneratedCourse, result.parsed)

    course.title = generated.title
    course.slug = slugify(generated.title)
    course.description = generated.description
    course.learning_objectives = generated.learning_objectives
    course.model_used = result.model
    course.prompt_template = COURSE_OUTLINE.name
    course.prompt_version = COURSE_OUTLINE.version
    course.status = CourseStatus.review

    # Regeneration replaces the whole structure; cascade removes the old modules/lessons.
    for module in list(course.modules):
        await session.delete(module)
    await session.flush()

    for m_index, module_data in enumerate(generated.modules):
        module = Module(
            organization_id=course.organization_id,
            course_id=course.id,
            title=module_data.title,
            summary=module_data.summary,
            position=m_index,
            learning_objectives=module_data.learning_objectives,
        )
        session.add(module)
        await session.flush()
        session.add_all(
            Lesson(
                organization_id=course.organization_id,
                module_id=module.id,
                title=lesson.title,
                position=l_index,
                summary=lesson.summary,
                content=lesson.content,
                exercise=lesson.exercise,
                estimated_minutes=lesson.estimated_minutes,
            )
            for l_index, lesson in enumerate(module_data.lessons)
        )
    await session.flush()
    log.info("course_generated", course_id=str(course.id), modules=len(generated.modules))
    return course


async def regenerate_module(
    session: AsyncSession,
    gateway: AIGateway,
    course: Course,
    module: Module,
    instructions: str | None,
) -> Module:
    context = await _source_context(
        session, course.organization_id, [uuid.UUID(str(d)) for d in course.source_document_ids]
    )
    system, user = COURSE_OUTLINE.render(
        context=context[:16000],
        subject=f"{course.title} — rewrite only the module '{module.title}'."
        + (f" Instructions: {instructions}" if instructions else ""),
        module_count="1",
        audience="the existing course audience",
    )
    result = await gateway.complete(
        "course_module_regenerate",
        [ChatMessage("system", system), ChatMessage("user", user)],
        prompt=COURSE_OUTLINE,
        schema=GeneratedCourse,
        temperature=0.4,
        max_tokens=4000,
    )
    generated = cast(GeneratedCourse, result.parsed)
    replacement = generated.modules[0]

    module.title = replacement.title
    module.summary = replacement.summary
    module.learning_objectives = replacement.learning_objectives
    for lesson in list(module.lessons):
        await session.delete(lesson)
    await session.flush()
    session.add_all(
        Lesson(
            organization_id=course.organization_id,
            module_id=module.id,
            title=lesson.title,
            position=index,
            summary=lesson.summary,
            content=lesson.content,
            exercise=lesson.exercise,
            estimated_minutes=lesson.estimated_minutes,
        )
        for index, lesson in enumerate(replacement.lessons)
    )
    await session.flush()
    return module


# --------------------------------------------------------------------------- assessment
def validate_questions(questions: list[Any]) -> dict[str, Any]:
    """Rejects items that would be unfair to a learner. Returns a report; caller decides."""
    issues: list[dict[str, Any]] = []
    accepted: list[int] = []

    for index, question in enumerate(questions):
        problems: list[str] = []
        if question.type in ("short_answer", "scenario") and not (question.rubric or "").strip():
            problems.append("missing_rubric")
        if question.type == "multiple_choice":
            if question.correct_answer not in question.options:
                problems.append("correct_answer_not_in_options")
            if len(set(question.options)) != len(question.options):
                problems.append("duplicate_options")
            if any(marker in o.lower() for o in question.options for marker in AMBIGUOUS_MARKERS):
                problems.append("ambiguous_option")
        if question.type == "true_false" and question.correct_answer.lower() not in (
            "true",
            "false",
        ):
            problems.append("non_boolean_answer")
        if question.confidence < 0.5:
            problems.append("low_confidence")
        if len(question.source_reference.strip()) < 10:
            problems.append("unsupported_by_source")

        for other in accepted:
            similarity = SequenceMatcher(
                None, question.prompt.lower(), questions[other].prompt.lower()
            ).ratio()
            if similarity >= DUPLICATE_THRESHOLD:
                problems.append(f"duplicate_of_question_{other}")
                break

        if problems:
            issues.append({"index": index, "prompt": question.prompt[:120], "problems": problems})
        else:
            accepted.append(index)

    return {
        "total": len(questions),
        "accepted": accepted,
        "rejected": len(issues),
        "issues": issues,
    }


async def generate_assessment(
    session: AsyncSession,
    gateway: AIGateway,
    assessment: Assessment,
    *,
    document_ids: list[uuid.UUID],
    question_count: int,
    question_types: list[str],
    topics: list[str],
) -> Assessment:
    context = await _source_context(session, assessment.organization_id, document_ids)
    system, user = QUIZ_GENERATE.render(
        context=context[:20000],
        count=str(question_count),
        difficulty=assessment.difficulty.value,
        topics=", ".join(topics) or "the whole document",
        types=", ".join(question_types),
    )
    result = await gateway.complete(
        "assessment_generate",
        [ChatMessage("system", system), ChatMessage("user", user)],
        prompt=QUIZ_GENERATE,
        schema=GeneratedQuiz,
        temperature=0.3,
        max_tokens=6000,
    )
    generated = cast(GeneratedQuiz, result.parsed)
    report = validate_questions(generated.questions)
    if not report["accepted"]:
        raise ValidationFailed("Every generated question failed validation", {"report": report})

    for question in list(assessment.questions):
        await session.delete(question)
    await session.flush()

    for position, index in enumerate(report["accepted"]):
        item = generated.questions[index]
        session.add(
            Question(
                organization_id=assessment.organization_id,
                assessment_id=assessment.id,
                position=position,
                type=QuestionType(item.type),
                prompt=item.prompt,
                options=item.options,
                correct_answer=item.correct_answer,
                explanation=item.explanation,
                rubric=item.rubric,
                difficulty=Difficulty(item.difficulty),
                learning_objective=item.learning_objective,
                source_reference=item.source_reference,
                confidence=item.confidence,
                points=2 if item.type in ("short_answer", "scenario") else 1,
            )
        )
    assessment.model_used = result.model
    assessment.prompt_version = QUIZ_GENERATE.version
    assessment.validation_report = report
    await session.flush()
    log.info(
        "assessment_generated",
        assessment_id=str(assessment.id),
        accepted=len(report["accepted"]),
        rejected=report["rejected"],
    )
    return assessment


async def require_course(
    session: AsyncSession, organization_id: uuid.UUID, course_id: uuid.UUID
) -> Course:
    course = await session.get(Course, course_id)
    if course is None or course.organization_id != organization_id:
        raise NotFound("Course not found")
    return course
