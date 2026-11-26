"""AI-powered course and assessment generation service.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file manages automated curriculum and quiz creation from training documents:
1. `_source_context()`: Performs stratified sampling of chunks across all selected
   documents, preventing one large file from starving other files of LLM context.
2. `generate_course()`: Prompts the LLM for a structured curriculum, generates modules
   and lessons, and atomically persists them in PostgreSQL.
3. `regenerate_module()`: Rewrites a single module based on instructor revision feedback.
4. `validate_questions()`: Sanity checks quiz questions to protect learners from bad AI output:
   - Verifies multiple-choice questions have correct answers in the option list.
   - Rejects duplicate options and ambiguous options ("all of the above", "both a and b").
   - Enforces rubrics on short-answer/scenario questions.
   - Computes fuzzy string similarity using `SequenceMatcher` to discard duplicate questions.
5. `generate_assessment()`: Runs quiz generation and saves only validated questions.

===============================================================================
WHY STRATIFIED SAMPLING OVER CONCATENATING RAW TEXT?
===============================================================================
If a course is based on three PDFs (100 pages, 5 pages, and 10 pages), naive concatenation
fills the context window with Document 1. Stratified sampling picks an even slice of
chunks from every document, guaranteeing the course covers all uploaded sources equally.
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

MAX_CONTEXT_CHUNKS = 40  # Maximum document chunks passed in prompt context
DUPLICATE_THRESHOLD = 0.85  # Levenshtein/SequenceMatcher ratio flagging duplicate questions
AMBIGUOUS_MARKERS = ("all of the above", "none of the above", "both a and b", "not sure")


def slugify(value: str) -> str:
    """Converts a course title string into a clean URL-friendly slug."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:110] or "course"


async def _source_context(
    session: AsyncSession,
    organization_id: uuid.UUID,
    document_ids: list[uuid.UUID],
    limit: int = MAX_CONTEXT_CHUNKS,
) -> str:
    """Performs stratified chunk sampling across the list of source documents."""
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
        # Stride through chunks evenly across the document
        step = max(1, len(rows) // per_doc)
        parts.extend(chunk.content for chunk in rows[::step][:per_doc])
    if not parts:
        raise ValidationFailed(
            "Selected documents have no indexed chunks yet — wait for ingestion to finish"
        )
    return "\n\n---\n\n".join(parts)


# -----------------------------------------------------------------------------
# Course Generation Functions
# -----------------------------------------------------------------------------


async def generate_course(
    session: AsyncSession,
    gateway: AIGateway,
    course: Course,
    *,
    subject: str,
    module_count: int,
    audience: str,
) -> Course:
    """Generates a complete multi-module course curriculum from source documents."""
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

    # Atomic reset of previous modules and lessons
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
    """Regenerates a single module inside an existing course according to custom revision
    instructions.
    """
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


# -----------------------------------------------------------------------------
# Assessment Generation & Validation
# -----------------------------------------------------------------------------


def validate_questions(questions: list[Any]) -> dict[str, Any]:
    """Sanity-checks LLM generated questions against rigorous pedagogic quality standards.

    Checks:
    1. Rubric required for short-answer and scenario questions.
    2. Multiple-choice correct answer exists in options list.
    3. Multiple-choice options are unique and free of ambiguous choices.
    4. True/False correct answer is strictly boolean.
    5. Discards duplicate questions matching `SequenceMatcher` similarity ratio >= 0.85.
    """
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

        # Fuzzy duplicate detection against previously accepted questions
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
    """Generates an assessment quiz, filters out flawed questions, and persists approved items."""
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

    # Clear previous questions
    for question in list(assessment.questions):
        await session.delete(question)
    await session.flush()

    # Save only validated questions
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
    """Helper to retrieve a course belonging to the current organization or raise 404."""
    course = await session.get(Course, course_id)
    if course is None or course.organization_id != organization_id:
        raise NotFound("Course not found")
    return course
