"""Pydantic schemas the model must conform to.

These are passed to the provider as a JSON schema *and* re-validated on the way back. The
gateway never returns unvalidated model output to a caller.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class GeneratedLesson(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    summary: str = Field(min_length=10)
    content: str = Field(min_length=30, description="Lesson body in markdown")
    exercise: str = Field(min_length=10, description="A practical exercise the learner can do")
    estimated_minutes: int = Field(ge=1, le=240)


class GeneratedModule(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    summary: str = Field(min_length=10)
    learning_objectives: list[str] = Field(min_length=1, max_length=8)
    lessons: list[GeneratedLesson] = Field(min_length=1, max_length=10)


class GeneratedCourse(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=20)
    learning_objectives: list[str] = Field(min_length=2, max_length=12)
    modules: list[GeneratedModule] = Field(min_length=1, max_length=12)


class GeneratedQuestion(BaseModel):
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
        if info.data.get("type") == "multiple_choice" and len(v) < 3:
            raise ValueError("multiple_choice questions need at least 3 options")
        return v


class GeneratedQuiz(BaseModel):
    title: str = Field(min_length=3)
    questions: list[GeneratedQuestion] = Field(min_length=1, max_length=50)


class QuestionEvaluation(BaseModel):
    question_index: int = Field(ge=0)
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    rationale: str = Field(min_length=10)
    confidence: float = Field(ge=0, le=1)


class GeneratedEvaluation(BaseModel):
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=20)
    strengths: list[str] = Field(default_factory=list, max_length=8)
    missing_concepts: list[str] = Field(default_factory=list, max_length=8)
    suggested_resources: list[str] = Field(default_factory=list, max_length=6)


class TicketTriage(BaseModel):
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
    output_type: str
    title: str = Field(min_length=3)
    body: str = Field(min_length=30)
    bullet_points: list[str] = Field(default_factory=list, max_length=20)
