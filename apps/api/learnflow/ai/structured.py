"""Pydantic schemas for LLM structured output validation.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file defines the strict Pydantic schemas that model outputs must conform to:
1. `GeneratedCourse`, `GeneratedModule`, `GeneratedLesson`: Complete curriculum structures.
2. `GeneratedQuiz`, `GeneratedQuestion`: Assessment items with field validators ensuring
   multiple choice questions have at least 3 options and valid difficulty patterns.
3. `GeneratedEvaluation`, `QuestionEvaluation`: Auto-grading output containing score,
   rationale, strengths, and missing concepts.
4. `TicketTriage`: Support ticket category, sentiment, urgency, and escalation flag.
5. `TransformedContent`: Repurposed educational assets (summaries, flashcards, FAQs).

===============================================================================
HOW IT ENFORCES QUALITY GUARDRAILS:
===============================================================================
These schemas are converted to JSON schemas (`schema.model_json_schema()`), passed to
the LLM API, and validated upon receipt by `AIGateway`. If a model returns invalid JSON,
missing fields, or malformed choices, `AIGateway._parse_or_repair()` catches the
`ValidationError` and sends a repair prompt.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


# -----------------------------------------------------------------------------
# Course & Curriculum Generation Schemas
# -----------------------------------------------------------------------------

class GeneratedLesson(BaseModel):
    """An individual lesson within a generated module."""
    title: str = Field(min_length=3, max_length=300)
    summary: str = Field(min_length=10)
    content: str = Field(min_length=30, description="Lesson body in markdown")
    exercise: str = Field(min_length=10, description="A practical exercise the learner can do")
    estimated_minutes: int = Field(ge=1, le=240)


class GeneratedModule(BaseModel):
    """A generated course module containing multiple lessons."""
    title: str = Field(min_length=3, max_length=300)
    summary: str = Field(min_length=10)
    learning_objectives: list[str] = Field(min_length=1, max_length=8)
    lessons: list[GeneratedLesson] = Field(min_length=1, max_length=10)


class GeneratedCourse(BaseModel):
    """The root payload schema for full course generation."""
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=20)
    learning_objectives: list[str] = Field(min_length=2, max_length=12)
    modules: list[GeneratedModule] = Field(min_length=1, max_length=12)


# -----------------------------------------------------------------------------
# Assessment & Question Schemas
# -----------------------------------------------------------------------------

class GeneratedQuestion(BaseModel):
    """A generated assessment item with validation rules."""
    type: str = Field(pattern="^(multiple_choice|true_false|short_answer|scenario)$")
    prompt: str = Field(min_length=10)
    options: list[str] = Field(default_factory=list, max_length=6)
    correct_answer: str = Field(min_length=1)
    explanation: str = Field(min_length=10)
    difficulty: str = Field(pattern="^(beginner|intermediate|advanced)$")
    learning_objective: str = Field(min_length=3)
    source_reference: str = Field(min_length=3, description="Sentence from the source material")
    confidence: float = Field(ge=0, le=1)
    rubric: str | None = Field(default=None, description="Required for short_answer and scenario")

    @field_validator("options")
    @classmethod
    def _mc_needs_options(cls, v: list[str], info) -> list[str]:
        """Validates that multiple choice questions provide at least 3 distinct answer options."""
        if info.data.get("type") == "multiple_choice" and len(v) < 3:
            raise ValueError("multiple_choice questions need at least 3 options")
        return v


class GeneratedQuiz(BaseModel):
    """The root payload schema for assessment generation."""
    title: str = Field(min_length=3)
    questions: list[GeneratedQuestion] = Field(min_length=1, max_length=50)


# -----------------------------------------------------------------------------
# Evaluation & Grading Schemas
# -----------------------------------------------------------------------------

class QuestionEvaluation(BaseModel):
    """Grading breakdown for an individual subjective question."""
    question_index: int = Field(ge=0)
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    rationale: str = Field(min_length=10)
    confidence: float = Field(ge=0, le=1)


class GeneratedEvaluation(BaseModel):
    """Overall evaluation output returned by the LLM grading service."""
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=20)
    strengths: list[str] = Field(default_factory=list, max_length=8)
    missing_concepts: list[str] = Field(default_factory=list, max_length=8)
    suggested_resources: list[str] = Field(default_factory=list, max_length=6)


# -----------------------------------------------------------------------------
# Support Ticket & Content Transform Schemas
# -----------------------------------------------------------------------------

class TicketTriage(BaseModel):
    """Automated classification and response draft for support tickets."""
    category: str = Field(
        pattern="^(course_access|payment_issue|technical_problem|assessment_query"
        "|certificate_issue|general_learning)$"
    )
    urgency: str = Field(pattern="^(low|normal|high|critical)$")
    sentiment: str = Field(pattern="^(positive|neutral|negative|frustrated)$")
    confidence: float = Field(ge=0, le=1)
    explanation: str = Field(min_length=10)
    suggested_response: str = Field(min_length=20)
    escalate: bool


TICKET_CATEGORIES = [
    "course_access",
    "payment_issue",
    "technical_problem",
    "assessment_query",
    "certificate_issue",
    "general_learning",
]


class TransformedContent(BaseModel):
    """Repurposed training document format (FAQ, Flashcard, Executive Summary)."""
    output_type: str
    title: str = Field(min_length=3)
    body: str = Field(min_length=30)
    bullet_points: list[str] = Field(default_factory=list, max_length=20)
