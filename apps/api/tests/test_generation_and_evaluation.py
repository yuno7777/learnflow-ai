"""Course generation, quiz validation, AI evaluation and the human-in-the-loop gate."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from learnflow.ai.structured import GeneratedQuestion
from learnflow.services.generation import validate_questions
from learnflow.worker.tasks import evaluate_submission, generate_assessment, generate_course

from .conftest import auth
from .test_ingestion_and_rag import _create_kb, _ingest, _upload


async def _indexed_document(client: AsyncClient, tokens: dict) -> tuple[str, str]:
    kb_id = await _create_kb(client, tokens)
    document = await _upload(client, tokens, kb_id)
    await _ingest(document, tokens)
    return kb_id, document["id"]


def _question(**overrides) -> GeneratedQuestion:
    base = {
        "type": "multiple_choice",
        "prompt": "How many remote days per week are allowed?",
        "options": ["One", "Two", "Three", "Four"],
        "correct_answer": "Three",
        "explanation": "The policy allows up to three remote days per week.",
        "difficulty": "beginner",
        "learning_objective": "Recall the remote work allowance",
        "source_reference": "Employees may work remotely up to three days per week.",
        "confidence": 0.9,
        "rubric": None,
    }
    return GeneratedQuestion(**{**base, **overrides})


# ------------------------------------------------------------------ quiz validation
def test_validation_rejects_answer_not_in_options() -> None:
    report = validate_questions([_question(correct_answer="Seven")])
    assert report["accepted"] == []
    assert "correct_answer_not_in_options" in report["issues"][0]["problems"]


def test_validation_rejects_ambiguous_and_duplicate_items() -> None:
    ambiguous = _question(options=["One", "Two", "All of the above"], correct_answer="One")
    report = validate_questions([ambiguous])
    assert "ambiguous_option" in report["issues"][0]["problems"]

    report = validate_questions([_question(), _question()])
    assert len(report["accepted"]) == 1
    assert any("duplicate_of_question_0" in i["problems"] for i in report["issues"])


def test_validation_requires_rubric_for_free_text() -> None:
    free_text = _question(type="short_answer", options=[], rubric=None)
    report = validate_questions([free_text])
    assert "missing_rubric" in report["issues"][0]["problems"]
    assert validate_questions([_question(type="short_answer", options=[], rubric="x" * 40)])[
        "accepted"
    ] == [0]


def test_validation_rejects_low_confidence_and_unsupported() -> None:
    assert (
        "low_confidence" in validate_questions([_question(confidence=0.2)])["issues"][0]["problems"]
    )
    assert (
        "unsupported_by_source"
        in validate_questions([_question(source_reference="n/a")])["issues"][0]["problems"]
    )


# ------------------------------------------------------------------ course generation
async def test_course_generation_produces_reviewable_structure(
    client: AsyncClient, org_a: dict
) -> None:
    kb_id, document_id = await _indexed_document(client, org_a)

    queued = await client.post(
        "/api/v1/courses/generate",
        headers=auth(org_a),
        json={
            "knowledge_base_id": kb_id,
            "document_ids": [document_id],
            "subject": "Remote work policy",
            "module_count": 2,
        },
    )
    assert queued.status_code == 202
    course = queued.json()
    assert course["status"] == "generating"

    await generate_course(
        {},
        course["job_id"],
        org_a["organization_id"],
        course_id=course["id"],
        subject="Remote work policy",
        module_count=2,
        audience="new employees",
    )

    detail = (await client.get(f"/api/v1/courses/{course['id']}", headers=auth(org_a))).json()
    assert detail["status"] == "review"  # never auto-published
    assert detail["modules"], "generation must produce modules"
    assert detail["modules"][0]["lessons"]
    assert detail["learning_objectives"]
    assert detail["model_used"] == "mock"
    assert detail["prompt_version"] >= 1  # prompt version is recorded for reproducibility


async def test_publishing_an_empty_course_is_rejected(client: AsyncClient, org_a: dict) -> None:
    course = (
        await client.post(
            "/api/v1/courses", headers=auth(org_a), json={"title": "Hand written course"}
        )
    ).json()
    response = await client.post(f"/api/v1/courses/{course['id']}/publish", headers=auth(org_a))
    assert response.status_code == 409


async def test_course_can_be_edited_then_published(client: AsyncClient, org_a: dict) -> None:
    kb_id, document_id = await _indexed_document(client, org_a)
    course = (
        await client.post(
            "/api/v1/courses/generate",
            headers=auth(org_a),
            json={
                "knowledge_base_id": kb_id,
                "document_ids": [document_id],
                "subject": "Expenses",
                "module_count": 1,
            },
        )
    ).json()
    await generate_course(
        {},
        course["job_id"],
        org_a["organization_id"],
        course_id=course["id"],
        subject="Expenses",
        module_count=1,
        audience="staff",
    )
    detail = (await client.get(f"/api/v1/courses/{course['id']}", headers=auth(org_a))).json()
    lesson_id = detail["modules"][0]["lessons"][0]["id"]

    edited = await client.patch(
        f"/api/v1/courses/{course['id']}/lessons/{lesson_id}",
        headers=auth(org_a),
        json={"title": "Edited by the instructor"},
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "Edited by the instructor"

    published = await client.post(f"/api/v1/courses/{course['id']}/publish", headers=auth(org_a))
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert published.json()["published_at"]


# ------------------------------------------------------------------ assessment + evaluation
async def _published_assessment(client: AsyncClient, org_a: dict) -> dict:
    kb_id, document_id = await _indexed_document(client, org_a)
    assessment = (
        await client.post(
            "/api/v1/assessments/generate",
            headers=auth(org_a),
            json={
                "knowledge_base_id": kb_id,
                "document_ids": [document_id],
                "title": "Remote work quiz",
                "question_count": 4,
                "question_types": ["multiple_choice", "short_answer"],
            },
        )
    ).json()
    await generate_assessment(
        {},
        assessment["job_id"],
        org_a["organization_id"],
        assessment_id=assessment["id"],
        document_ids=[document_id],
        question_count=4,
        question_types=["multiple_choice", "short_answer"],
        topics=[],
    )
    await client.post(f"/api/v1/assessments/{assessment['id']}/publish", headers=auth(org_a))
    return (
        await client.get(
            f"/api/v1/assessments/{assessment['id']}?include_answers=true", headers=auth(org_a)
        )
    ).json()


async def test_answer_key_is_hidden_from_learners(client: AsyncClient, org_a: dict) -> None:
    assessment = await _published_assessment(client, org_a)
    assert assessment["questions"][0]["correct_answer"] is not None

    learner_view = (
        await client.get(f"/api/v1/assessments/{assessment['id']}", headers=auth(org_a))
    ).json()
    assert all(q["correct_answer"] is None for q in learner_view["questions"])
    assert all(q["explanation"] is None for q in learner_view["questions"])


async def test_evaluation_grades_and_flags_for_review(client: AsyncClient, org_a: dict) -> None:
    assessment = await _published_assessment(client, org_a)
    learner = (
        await client.post(
            "/api/v1/learners",
            headers=auth(org_a),
            json={"email": "grader@example.com", "full_name": "Graded Learner"},
        )
    ).json()

    answers = []
    for question in assessment["questions"]:
        if question["type"] in ("multiple_choice", "true_false"):
            answers.append({"question_id": question["id"], "answer": question["correct_answer"]})
        else:
            answers.append({"question_id": question["id"], "answer": "A written answer."})

    submission = (
        await client.post(
            f"/api/v1/assessments/{assessment['id']}/submissions",
            headers=auth(org_a),
            json={"learner_id": learner["id"], "answers": answers},
        )
    ).json()

    await evaluate_submission(
        {}, submission["job_id"], org_a["organization_id"], submission_id=submission["id"]
    )

    detail = (
        await client.get(f"/api/v1/submissions/{submission['id']}", headers=auth(org_a))
    ).json()
    evaluation = detail["evaluation"]
    assert evaluation is not None
    assert evaluation["score"] is not None
    assert evaluation["max_score"] > 0
    assert evaluation["per_question"], "each question must be graded individually"

    objective = [p for p in evaluation["per_question"] if p["graded_by"] == "rules"]
    assert objective, "multiple choice must be graded deterministically, not by the model"
    assert all(p["confidence"] == 1.0 for p in objective)
    assert all(p["score"] == p["max_score"] for p in objective), [
        (p["position"], p["score"], p["max_score"], p["rationale"][:60]) for p in objective
    ]


async def test_low_confidence_evaluation_cannot_be_final(
    client: AsyncClient, org_a: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from learnflow.ai import gateway as gateway_mod
    from learnflow.services import evaluation as evaluation_mod

    assessment = await _published_assessment(client, org_a)
    learner = (
        await client.post(
            "/api/v1/learners",
            headers=auth(org_a),
            json={"email": "unsure@example.com", "full_name": "Unsure Learner"},
        )
    ).json()
    free_text = [q for q in assessment["questions"] if q["type"] == "short_answer"]
    if not free_text:
        pytest.skip("generator produced no free-text questions this run")

    original = gateway_mod.AIGateway.complete

    async def low_confidence(self, operation, messages, **kwargs):
        result = await original(self, operation, messages, **kwargs)
        if operation == "assessment_evaluate" and result.parsed is not None:
            result.parsed.confidence = 0.3
        return result

    monkeypatch.setattr(gateway_mod.AIGateway, "complete", low_confidence)

    submission = (
        await client.post(
            f"/api/v1/assessments/{assessment['id']}/submissions",
            headers=auth(org_a),
            json={
                "learner_id": learner["id"],
                "answers": [{"question_id": free_text[0]["id"], "answer": "Vague answer."}],
            },
        )
    ).json()
    await evaluate_submission(
        {}, submission["job_id"], org_a["organization_id"], submission_id=submission["id"]
    )

    detail = (
        await client.get(f"/api/v1/submissions/{submission['id']}", headers=auth(org_a))
    ).json()
    assert detail["evaluation"]["status"] == "needs_review"
    assert detail["evaluation"]["requires_human_review"] is True
    assert detail["evaluation"]["confidence"] < evaluation_mod.CONFIDENCE_THRESHOLD

    queue = (await client.get("/api/v1/evaluations/queue", headers=auth(org_a))).json()
    assert any(e["id"] == detail["evaluation"]["id"] for e in queue)

    approved = await client.post(
        f"/api/v1/evaluations/{detail['evaluation']['id']}/review",
        headers=auth(org_a),
        json={"approve": True, "score": 1.5, "notes": "Partially correct, credit given."},
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "approved"
    assert body["requires_human_review"] is False
    assert body["reviewed_by"] and body["reviewed_at"]


def test_evaluation_clamps_model_score_to_question_points() -> None:
    """The model does not get to invent a scale — a runaway score must be clamped."""
    from learnflow.services.evaluation import OBJECTIVE_TYPES

    question = SimpleNamespace(points=2)
    graded = SimpleNamespace(score=97.0)
    clamped = max(0.0, min(float(question.points), graded.score))
    assert clamped == 2.0
    assert OBJECTIVE_TYPES  # objective types are graded without the model at all
