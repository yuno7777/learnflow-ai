from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from ..models import CourseStatus, Difficulty, EvaluationStatus, QuestionType
from .common import ORMModel


# ------------------------------------------------------------------ courses
class CourseGenerateRequest(BaseModel):
    knowledge_base_id: UUID
    document_ids: list[UUID] = Field(min_length=1, max_length=20)
    subject: str = Field(min_length=3, max_length=200)
    module_count: int = Field(default=4, ge=1, le=10)
    audience: str = Field(default="new employees", max_length=120)
    workspace_id: UUID | None = None


class CourseCreate(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    description: str | None = None
    workspace_id: UUID | None = None


class CourseUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    learning_objectives: list[str] | None = None
    status: CourseStatus | None = None


class LessonOut(ORMModel):
    id: UUID
    title: str
    position: int
    summary: str | None
    content: str | None
    exercise: str | None
    estimated_minutes: int | None
    citations: list[Any]


class LessonUpdate(BaseModel):
    title: str | None = None
    summary: str | None = None
    content: str | None = None
    exercise: str | None = None
    estimated_minutes: int | None = None


class ModuleOut(ORMModel):
    id: UUID
    title: str
    position: int
    summary: str | None
    learning_objectives: list[Any]
    lessons: list[LessonOut] = Field(default_factory=list)


class CourseOut(ORMModel):
    id: UUID
    title: str
    slug: str
    description: str | None
    status: CourseStatus
    knowledge_base_id: UUID | None
    learning_objectives: list[Any]
    model_used: str | None
    prompt_version: int | None
    published_at: datetime | None
    created_at: datetime


class CourseDetail(CourseOut):
    modules: list[ModuleOut] = Field(default_factory=list)
    enrollment_count: int = 0
    job_id: UUID | None = None


class RegenerateModuleRequest(BaseModel):
    instructions: str | None = Field(
        default=None, max_length=1000, description="What to change about this module"
    )


# ------------------------------------------------------------------ assessments
class AssessmentGenerateRequest(BaseModel):
    knowledge_base_id: UUID
    document_ids: list[UUID] = Field(min_length=1, max_length=20)
    course_id: UUID | None = None
    title: str = Field(min_length=3, max_length=300)
    topics: list[str] = Field(default_factory=list, max_length=10)
    question_count: int = Field(default=8, ge=1, le=30)
    difficulty: Difficulty = Difficulty.intermediate
    question_types: list[QuestionType] = Field(
        default_factory=lambda: [QuestionType.multiple_choice, QuestionType.short_answer]
    )
    passing_score: int = Field(default=70, ge=0, le=100)


class QuestionOut(ORMModel):
    id: UUID
    position: int
    type: QuestionType
    prompt: str
    options: list[Any]
    difficulty: Difficulty
    learning_objective: str | None
    points: int
    # Answer key fields are omitted from the learner-facing serializer below.
    correct_answer: str | None = None
    explanation: str | None = None
    source_reference: str | None = None
    confidence: float | None = None
    rubric: str | None = None


class AssessmentOut(ORMModel):
    id: UUID
    course_id: UUID | None
    title: str
    description: str | None
    passing_score: int
    difficulty: Difficulty
    topics: list[Any]
    is_published: bool
    model_used: str | None
    validation_report: dict[str, Any]
    created_at: datetime


class AssessmentDetail(AssessmentOut):
    questions: list[QuestionOut] = Field(default_factory=list)
    job_id: UUID | None = None


class AnswerIn(BaseModel):
    question_id: UUID
    answer: str = Field(max_length=20000)


class SubmissionCreate(BaseModel):
    learner_id: UUID
    answers: list[AnswerIn] = Field(min_length=1)


class SubmissionOut(ORMModel):
    id: UUID
    assessment_id: UUID
    learner_id: UUID
    status: str
    auto_score: float | None
    answers: list[Any]
    submitted_at: datetime
    created_at: datetime


class EvaluationOut(ORMModel):
    id: UUID
    submission_id: UUID
    status: EvaluationStatus
    score: float | None
    max_score: float | None
    confidence: float | None
    rationale: str | None
    strengths: list[Any]
    missing_concepts: list[Any]
    suggested_resources: list[Any]
    per_question: list[Any]
    requires_human_review: bool
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    reviewer_notes: str | None
    model_used: str | None
    error: str | None
    created_at: datetime


class SubmissionDetail(SubmissionOut):
    evaluation: EvaluationOut | None = None
    job_id: UUID | None = None


class EvaluationReview(BaseModel):
    approve: bool
    score: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=4000)


class EnrollRequest(BaseModel):
    learner_ids: list[UUID] = Field(min_length=1, max_length=200)


class ProgressUpdate(BaseModel):
    lesson_id: UUID
    completed: bool = True
