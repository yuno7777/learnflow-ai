"""Courses, modules, lessons, assessments, submissions, and AI evaluation models.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file defines the educational and grading data layer of LearnFlow AI:
1. `CourseStatus` & `Course`: The top-level training course entity, tracking source
   document references, learning objectives, and prompt versioning.
2. `Module` & `Lesson`: Ordered hierarchical curriculum structure.
3. `Enrollment`: Student enrollment linking `Learner` to `Course` with progress percentage.
4. `Assessment` & `Question`: Generated quizzes (multiple choice, true/false, short answer,
scenario)
   with passing scores and validation reports.
5. `Submission`: Student-submitted answers for an assessment.
6. `Evaluation`: Detailed auto-grading record, confidence score, per-question breakdown,
   and the `requires_human_review` gate.

===============================================================================
EVALUATION & HUMAN REVIEW ARCHITECTURE:
===============================================================================
- Objective questions (multiple choice, true/false) are graded in code with 1.0 confidence.
- Subjective questions (essays, scenarios) are graded by an LLM against rubrics.
- If the minimum confidence score across all questions is < 0.75, `requires_human_review`
  is set to `True` and `status` becomes `needs_review`.
- The evaluation remains provisional until an instructor/reviewer manually approves or
  overrides the score via `POST /api/v1/evaluations/{id}/review`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantEntity


def _enum(e, n):
    """Helper to create cross-dialect SQLAlchemy Enum columns."""
    return SAEnum(e, native_enum=False, length=n, values_callable=lambda x: [m.value for m in x])


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------


class CourseStatus(str, enum.Enum):
    draft = "draft"  # Newly created or manually authored
    generating = "generating"  # Background AI worker is currently generating modules/lessons
    review = "review"  # Generated, awaiting instructor approval/publishing
    published = "published"  # Active and visible to enrolled learners in /learn portal
    archived = "archived"  # Retired course
    failed = "failed"  # AI generation failed


class QuestionType(str, enum.Enum):
    multiple_choice = "multiple_choice"  # Graded in code (0 AI cost)
    true_false = "true_false"  # Graded in code (0 AI cost)
    short_answer = "short_answer"  # Graded via LLM rubric
    scenario = "scenario"  # Graded via LLM rubric


class Difficulty(str, enum.Enum):
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"


class EvaluationStatus(str, enum.Enum):
    pending = "pending"  # Evaluation queued
    ai_completed = "ai_completed"  # Graded by AI with high confidence (>= 0.75)
    needs_review = "needs_review"  # Flagged for human review (confidence < 0.75)
    approved = "approved"  # Approved/overridden by human reviewer
    rejected = "rejected"  # Rejected by reviewer
    failed = "failed"  # Evaluation pipeline error


# -----------------------------------------------------------------------------
# Course & Curriculum Hierarchy Models
# -----------------------------------------------------------------------------


class Course(TenantEntity):
    """Top-level course entity containing modules and lessons."""

    __tablename__ = "courses"

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[CourseStatus] = mapped_column(
        _enum(CourseStatus, 20), default=CourseStatus.draft, nullable=False
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    knowledge_base_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="SET NULL")
    )
    source_document_ids: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    learning_objectives: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    generation_job_id: Mapped[str | None] = mapped_column(String(80))
    prompt_template: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[int | None] = mapped_column(Integer)
    model_used: Mapped[str | None] = mapped_column(String(120))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )

    modules: Mapped[list[Module]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
        order_by="Module.position",
        lazy="selectin",
    )


class Module(TenantEntity):
    """A chapter or major section inside a course."""

    __tablename__ = "modules"

    course_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    learning_objectives: Mapped[list[Any]] = mapped_column(default=list, nullable=False)

    course: Mapped[Course] = relationship(back_populates="modules")
    lessons: Mapped[list[Lesson]] = relationship(
        back_populates="module",
        cascade="all, delete-orphan",
        order_by="Lesson.position",
        lazy="selectin",
    )


class Lesson(TenantEntity):
    """An individual learning unit containing markdown content, exercises, and citations."""

    __tablename__ = "lessons"

    module_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("modules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    exercise: Mapped[str | None] = mapped_column(Text)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer)
    citations: Mapped[list[Any]] = mapped_column(default=list, nullable=False)

    module: Mapped[Module] = relationship(back_populates="lessons")


class Enrollment(TenantEntity):
    """Maps a learner to a course, tracking progress and lesson completions."""

    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("learner_id", "course_id", name="uq_enrollment"),)

    learner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    completed_lesson_ids: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# -----------------------------------------------------------------------------
# Assessment, Submission, and Evaluation Models
# -----------------------------------------------------------------------------


class Assessment(TenantEntity):
    """A quiz or exam testing comprehension of course concepts."""

    __tablename__ = "assessments"

    course_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    passing_score: Mapped[int] = mapped_column(Integer, default=70, nullable=False)
    difficulty: Mapped[Difficulty] = mapped_column(
        _enum(Difficulty, 20), default=Difficulty.intermediate, nullable=False
    )
    topics: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    generation_job_id: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[int | None] = mapped_column(Integer)
    model_used: Mapped[str | None] = mapped_column(String(120))
    validation_report: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)

    questions: Mapped[list[Question]] = relationship(
        back_populates="assessment",
        cascade="all, delete-orphan",
        order_by="Question.position",
        lazy="selectin",
    )


class Question(TenantEntity):
    """An individual question within an assessment."""

    __tablename__ = "questions"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[QuestionType] = mapped_column(_enum(QuestionType, 24), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    correct_answer: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    rubric: Mapped[str | None] = mapped_column(Text)
    difficulty: Mapped[Difficulty] = mapped_column(_enum(Difficulty, 20), nullable=False)
    learning_objective: Mapped[str | None] = mapped_column(String(500))
    source_reference: Mapped[str | None] = mapped_column(String(500))
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    points: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    assessment: Mapped[Assessment] = relationship(back_populates="questions")


class Submission(TenantEntity):
    """A student's submitted answers to an assessment."""

    __tablename__ = "submissions"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    learner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("learners.id", ondelete="CASCADE"), nullable=False, index=True
    )
    answers: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    auto_score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default="submitted", nullable=False)

    evaluation: Mapped[Evaluation | None] = relationship(
        back_populates="submission", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )


class Evaluation(TenantEntity):
    """The grading evaluation for a submission.

    Contains the overall score, AI confidence, strengths, missing concepts,
    and the `requires_human_review` gate.
    """

    __tablename__ = "evaluations"

    submission_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[EvaluationStatus] = mapped_column(
        _enum(EvaluationStatus, 20), default=EvaluationStatus.pending, nullable=False
    )
    score: Mapped[float | None] = mapped_column(Float)
    max_score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str | None] = mapped_column(Text)
    strengths: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    missing_concepts: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    suggested_resources: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    per_question: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer_notes: Mapped[str | None] = mapped_column(Text)
    model_used: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    submission: Mapped[Submission] = relationship(back_populates="evaluation")
