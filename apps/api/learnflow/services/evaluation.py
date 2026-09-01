"""AI-powered assessment evaluation service with Human-in-the-Loop (HITL) quality gates.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file scores learner assessment submissions:
1. Deterministic Python Grading: Objective questions (multiple-choice, true/false) and
   blank submissions are scored strictly in Python code with 1.0 confidence and $0 AI cost.
2. Subjective LLM Grading: Free-text answers (short-answer, scenario) are graded using
   `ANSWER_EVALUATE` prompt with structured rubrics and temperature=0.0.
3. Hard Human-in-the-Loop Gate (`CONFIDENCE_THRESHOLD = 0.75`):
   - If the lowest question confidence is < 0.75, the evaluation is automatically flagged
     with `requires_human_review = True` and status `needs_review`.
   - Low-confidence grades are NEVER presented as final until approved by an instructor.
4. Instructor Review Workflow (`review_evaluation`): Human reviewers can approve, reject,
   or adjust scores with review notes.

===============================================================================
WHY DETERMINISTIC FIRST, LLM SECOND?
===============================================================================
1. Calling an LLM to check if `"B" == "B"` wastes money and introduces latency.
2. Code-based string normalization (`_normalise`) is 100% accurate, instant, and free.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.base import ChatMessage
from ..ai.gateway import AIGateway
from ..ai.prompts import ANSWER_EVALUATE
from ..ai.structured import GeneratedEvaluation
from ..logging import get_logger
from ..models import (
    Evaluation,
    EvaluationStatus,
    Question,
    QuestionType,
    Submission,
)

log = get_logger(__name__)

# Minimum confidence required for automatic approval without human review
CONFIDENCE_THRESHOLD = 0.75
OBJECTIVE_TYPES = {QuestionType.multiple_choice, QuestionType.true_false}


def _normalise(value: str) -> str:
    """Normalizes whitespace and casing for exact-match comparisons."""
    return " ".join(value.strip().lower().split())


async def evaluate_submission(
    session: AsyncSession, gateway: AIGateway, submission: Submission
) -> Evaluation:
    """Evaluates an assessment submission combining rule-based and AI grading.

    Algorithm:
    1. Loads all questions belonging to the assessment.
    2. Loops through questions:
       - Objective question -> Exact match comparison in Python -> Score awarded, confidence 1.0.
       - Blank answer -> Score 0, confidence 1.0.
       - Free-text answer -> Evaluated by LLM against question rubric.
    3. Calculates total score and finds minimum question confidence.
    4. If confidence < 0.75 -> status = 'needs_review'.
       Else -> status = 'ai_completed'.
    """
    questions = {
        q.id: q
        for q in (
            await session.execute(
                select(Question).where(Question.assessment_id == submission.assessment_id)
            )
        )
        .scalars()
        .all()
    }
    evaluation = submission.evaluation or Evaluation(
        organization_id=submission.organization_id, submission_id=submission.id
    )
    if evaluation.id is None:
        session.add(evaluation)

    answers = {str(a.get("question_id")): str(a.get("answer", "")) for a in submission.answers}
    per_question: list[dict[str, Any]] = []
    total_score = 0.0
    total_max = 0.0
    confidences: list[float] = []
    strengths: list[str] = []
    missing: list[str] = []
    resources: list[str] = []
    rationales: list[str] = []

    try:
        for question in sorted(questions.values(), key=lambda q: q.position):
            answer = answers.get(str(question.id), "")
            total_max += question.points

            # 1. Deterministic Rule-Based Grading (Objective Questions)
            if question.type in OBJECTIVE_TYPES:
                correct = _normalise(answer) == _normalise(question.correct_answer)
                score = float(question.points) if correct else 0.0
                total_score += score
                confidences.append(1.0)
                per_question.append(
                    {
                        "question_id": str(question.id),
                        "position": question.position,
                        "score": score,
                        "max_score": float(question.points),
                        "confidence": 1.0,
                        "graded_by": "rules",
                        "rationale": question.explanation if not correct else "Correct.",
                    }
                )
                if not correct:
                    missing.append(question.learning_objective or question.prompt[:80])
                continue

            # 2. Blank Free-Text Answers
            if not answer.strip():
                per_question.append(
                    {
                        "question_id": str(question.id),
                        "position": question.position,
                        "score": 0.0,
                        "max_score": float(question.points),
                        "confidence": 1.0,
                        "graded_by": "rules",
                        "rationale": "No answer submitted.",
                    }
                )
                missing.append(question.learning_objective or question.prompt[:80])
                confidences.append(1.0)
                continue

            # 3. Subjective LLM Grading with Rubrics
            system, user = ANSWER_EVALUATE.render(
                question=question.prompt,
                rubric=question.rubric or "Award full marks for a complete, accurate answer.",
                reference=question.correct_answer,
                answer=answer[:8000],
                max_points=str(question.points),
            )
            result = await gateway.complete(
                "assessment_evaluate",
                [ChatMessage("system", system), ChatMessage("user", user)],
                prompt=ANSWER_EVALUATE,
                schema=GeneratedEvaluation,
                temperature=0.0,
                max_tokens=1200,
            )
            graded = cast(GeneratedEvaluation, result.parsed)
            # Enforce hard clamp: model cannot award more points than question.points
            score = max(0.0, min(float(question.points), graded.score))
            total_score += score
            confidences.append(graded.confidence)
            strengths.extend(graded.strengths[:2])
            missing.extend(graded.missing_concepts[:2])
            resources.extend(graded.suggested_resources[:2])
            rationales.append(f"Q{question.position + 1}: {graded.rationale}")
            per_question.append(
                {
                    "question_id": str(question.id),
                    "position": question.position,
                    "score": score,
                    "max_score": float(question.points),
                    "confidence": graded.confidence,
                    "graded_by": "ai",
                    "model": result.model,
                    "rationale": graded.rationale,
                }
            )

        # 4. Synthesize Evaluation Summary and HITL Gate
        confidence = min(confidences) if confidences else 0.0
        evaluation.score = round(total_score, 2)
        evaluation.max_score = total_max
        evaluation.confidence = round(confidence, 3)
        evaluation.per_question = per_question
        evaluation.strengths = strengths[:8]
        evaluation.missing_concepts = missing[:8]
        evaluation.suggested_resources = resources[:6]
        evaluation.rationale = "\n".join(rationales) or "All questions graded by exact match."
        evaluation.model_used = None if not rationales else per_question[-1].get("model")
        evaluation.prompt_version = ANSWER_EVALUATE.version
        
        # Gate: Flag for human review if any question had low confidence (< 0.75)
        evaluation.requires_human_review = confidence < CONFIDENCE_THRESHOLD
        evaluation.status = (
            EvaluationStatus.needs_review
            if evaluation.requires_human_review
            else EvaluationStatus.ai_completed
        )
        evaluation.error = None
        submission.auto_score = round(100 * total_score / total_max, 2) if total_max else None
        submission.status = "evaluated"
    except Exception as exc:
        evaluation.status = EvaluationStatus.failed
        evaluation.error = f"{type(exc).__name__}: {exc}"[:2000]
        evaluation.requires_human_review = True
        submission.status = "evaluation_failed"
        log.exception("evaluation_failed", submission_id=str(submission.id))
        await session.flush()
        raise

    await session.flush()
    log.info(
        "submission_evaluated",
        submission_id=str(submission.id),
        score=evaluation.score,
        confidence=evaluation.confidence,
        needs_review=evaluation.requires_human_review,
    )
    return evaluation


async def review_evaluation(
    session: AsyncSession,
    evaluation: Evaluation,
    reviewer_id: uuid.UUID,
    *,
    approve: bool,
    score: float | None,
    notes: str | None,
) -> Evaluation:
    """Applies human instructor approval or manual grade override to an evaluation."""
    if score is not None:
        evaluation.score = max(0.0, min(score, evaluation.max_score or score))
    evaluation.status = EvaluationStatus.approved if approve else EvaluationStatus.rejected
    evaluation.requires_human_review = False
    evaluation.reviewed_by = reviewer_id
    evaluation.reviewed_at = datetime.now(UTC)
    evaluation.reviewer_notes = notes
    submission = await session.get(Submission, evaluation.submission_id)
    if submission is not None:
        submission.status = "approved" if approve else "rejected"
        if evaluation.max_score:
            submission.auto_score = round(100 * (evaluation.score or 0) / evaluation.max_score, 2)
    await session.flush()
    return evaluation
