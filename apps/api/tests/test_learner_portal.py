"""Learner self-scoping.

A learner login is the only principal in the system that is *inside* a tenant but must not
see the whole tenant. These tests pin that boundary: everything a learner reaches is their
own, and asking for somebody else's is indistinguishable from asking for something that does
not exist.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from learnflow.worker.tasks import evaluate_submission, generate_assessment

from .conftest import auth
from .test_generation_and_evaluation import _indexed_document

PASSWORD = "L3arnerPortal!pw"


async def _learner_with_login(client: AsyncClient, admin: dict, name: str) -> tuple[dict, dict]:
    """Create a learner, give it a portal login, and return (learner, learner tokens)."""
    learner = (
        await client.post(
            "/api/v1/learners",
            headers=auth(admin),
            json={"email": f"{name}-{uuid.uuid4().hex[:6]}@example.com", "full_name": name},
        )
    ).json()

    granted = await client.post(
        f"/api/v1/learners/{learner['id']}/access",
        headers=auth(admin),
        json={"password": PASSWORD},
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["account_created"] is True

    login = await client.post(
        "/api/v1/auth/login", json={"email": learner["email"], "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    assert login.json()["role"] == "learner"
    return learner, login.json()


async def test_granting_access_links_the_login_to_the_learner(
    client: AsyncClient, org_a: dict
) -> None:
    learner, tokens = await _learner_with_login(client, org_a, "Ada")

    me = (await client.get("/api/v1/auth/me", headers=auth(tokens))).json()
    assert me["role"] == "learner"
    assert me["learner_id"] == learner["id"], "the login must resolve to its learner record"
    # The learner role carries exactly one capability.
    assert me["permissions"] == ["rag:query"]


async def test_learner_sees_only_itself(client: AsyncClient, org_a: dict) -> None:
    mine, tokens = await _learner_with_login(client, org_a, "Ada")
    other = (
        await client.post(
            "/api/v1/learners",
            headers=auth(org_a),
            json={"email": "other@example.com", "full_name": "Other Learner"},
        )
    ).json()

    listing = (await client.get("/api/v1/learners", headers=auth(tokens))).json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == mine["id"]

    assert (await client.get(f"/api/v1/learners/{mine['id']}", headers=auth(tokens))).status_code == 200
    # Someone else's record is a 404, not a 403 - no confirming that the id exists.
    peek = await client.get(f"/api/v1/learners/{other['id']}", headers=auth(tokens))
    assert peek.status_code == 404


async def test_learner_cannot_manage_anything(client: AsyncClient, org_a: dict) -> None:
    _, tokens = await _learner_with_login(client, org_a, "Ada")

    for method, path, body in [
        ("post", "/api/v1/learners", {"email": "x@example.com", "full_name": "X Y"}),
        ("post", "/api/v1/knowledge-bases", {"name": "Mine"}),
        ("post", "/api/v1/courses", {"title": "Mine"}),
        ("post", "/api/v1/api-keys", {"name": "mine"}),
    ]:
        response = await getattr(client, method)(path, headers=auth(tokens), json=body)
        assert response.status_code == 403, f"{path} -> {response.status_code}"

    for path in ["/api/v1/analytics/overview", "/api/v1/audit-logs"]:
        assert (await client.get(path, headers=auth(tokens))).status_code == 403


async def test_learner_only_sees_enrolled_published_courses(
    client: AsyncClient, org_a: dict
) -> None:
    mine, tokens = await _learner_with_login(client, org_a, "Ada")

    draft = (
        await client.post("/api/v1/courses", headers=auth(org_a), json={"title": "Draft course"})
    ).json()
    # A published course they are *not* enrolled in must also stay invisible.
    unenrolled = (
        await client.post("/api/v1/courses", headers=auth(org_a), json={"title": "Someone else"})
    ).json()

    listing = (await client.get("/api/v1/courses", headers=auth(tokens))).json()
    assert listing["total"] == 0, "not enrolled in anything yet"
    assert (await client.get(f"/api/v1/courses/{draft['id']}", headers=auth(tokens))).status_code == 404
    assert (
        await client.get(f"/api/v1/courses/{unenrolled['id']}", headers=auth(tokens))
    ).status_code == 404


async def test_learner_cannot_submit_as_someone_else(client: AsyncClient, org_a: dict) -> None:
    kb_id, document_id = await _indexed_document(client, org_a)
    assessment = (
        await client.post(
            "/api/v1/assessments/generate",
            headers=auth(org_a),
            json={
                "knowledge_base_id": kb_id,
                "document_ids": [document_id],
                "title": "Quiz",
                "question_count": 2,
            },
        )
    ).json()
    await generate_assessment(
        {},
        assessment["job_id"],
        org_a["organization_id"],
        assessment_id=assessment["id"],
        document_ids=[document_id],
        question_count=2,
        question_types=["multiple_choice"],
        topics=[],
    )
    await client.post(f"/api/v1/assessments/{assessment['id']}/publish", headers=auth(org_a))
    detail = (
        await client.get(
            f"/api/v1/assessments/{assessment['id']}?include_answers=true", headers=auth(org_a)
        )
    ).json()
    answers = [
        {"question_id": q["id"], "answer": q["correct_answer"] or "x"} for q in detail["questions"]
    ]

    mine, tokens = await _learner_with_login(client, org_a, "Ada")
    victim = (
        await client.post(
            "/api/v1/learners",
            headers=auth(org_a),
            json={"email": "victim@example.com", "full_name": "Victim"},
        )
    ).json()

    # Submitting under another learner's id must not be possible.
    forged = await client.post(
        f"/api/v1/assessments/{assessment['id']}/submissions",
        headers=auth(tokens),
        json={"learner_id": victim["id"], "answers": answers},
    )
    assert forged.status_code == 404

    own = await client.post(
        f"/api/v1/assessments/{assessment['id']}/submissions",
        headers=auth(tokens),
        json={"learner_id": mine["id"], "answers": answers},
    )
    assert own.status_code == 202, own.text

    # And the submission list is scoped to them.
    listing = (await client.get("/api/v1/submissions", headers=auth(tokens))).json()
    assert listing["total"] == 1
    assert listing["items"][0]["learner_id"] == mine["id"]


async def test_learner_sees_own_result_after_evaluation(
    client: AsyncClient, org_a: dict
) -> None:
    kb_id, document_id = await _indexed_document(client, org_a)
    assessment = (
        await client.post(
            "/api/v1/assessments/generate",
            headers=auth(org_a),
            json={
                "knowledge_base_id": kb_id,
                "document_ids": [document_id],
                "title": "Quiz",
                "question_count": 2,
            },
        )
    ).json()
    await generate_assessment(
        {},
        assessment["job_id"],
        org_a["organization_id"],
        assessment_id=assessment["id"],
        document_ids=[document_id],
        question_count=2,
        question_types=["multiple_choice"],
        topics=[],
    )
    await client.post(f"/api/v1/assessments/{assessment['id']}/publish", headers=auth(org_a))
    detail = (
        await client.get(
            f"/api/v1/assessments/{assessment['id']}?include_answers=true", headers=auth(org_a)
        )
    ).json()

    mine, tokens = await _learner_with_login(client, org_a, "Ada")
    submission = (
        await client.post(
            f"/api/v1/assessments/{assessment['id']}/submissions",
            headers=auth(tokens),
            json={
                "learner_id": mine["id"],
                "answers": [
                    {"question_id": q["id"], "answer": q["correct_answer"] or "x"}
                    for q in detail["questions"]
                ],
            },
        )
    ).json()

    await evaluate_submission(
        {}, submission["job_id"], org_a["organization_id"], submission_id=submission["id"]
    )

    result = (
        await client.get(f"/api/v1/submissions/{submission['id']}", headers=auth(tokens))
    ).json()
    assert result["evaluation"] is not None
    assert result["evaluation"]["score"] is not None

    # The answer key stays hidden from the learner even after grading.
    learner_view = (
        await client.get(f"/api/v1/assessments/{assessment['id']}", headers=auth(tokens))
    ).json()
    assert all(q["correct_answer"] is None for q in learner_view["questions"])
    forbidden = await client.get(
        f"/api/v1/assessments/{assessment['id']}?include_answers=true", headers=auth(tokens)
    )
    assert forbidden.status_code == 403


async def test_learner_assistant_answers_only_about_itself(
    client: AsyncClient, org_a: dict
) -> None:
    await _indexed_document(client, org_a)
    mine, tokens = await _learner_with_login(client, org_a, "Ada")
    victim = (
        await client.post(
            "/api/v1/learners",
            headers=auth(org_a),
            json={"email": "victim2@example.com", "full_name": "Victim"},
        )
    ).json()

    forged = await client.post(
        "/api/v1/assistant/ask",
        headers=auth(tokens),
        json={"question": "How am I doing?", "learner_id": victim["id"]},
    )
    assert forged.status_code == 404

    own = await client.post(
        "/api/v1/assistant/ask",
        headers=auth(tokens),
        json={"question": "How am I doing?", "learner_id": mine["id"]},
    )
    assert own.status_code == 200, own.text
    assert own.json()["answer"]


async def test_granting_access_refuses_to_demote_staff(client: AsyncClient, org_a: dict) -> None:
    """An instructor who happens to share the learner's email must not be silently demoted."""
    staff_email = f"instructor-{uuid.uuid4().hex[:6]}@example.com"
    await client.post(
        "/api/v1/organizations/current/members",
        headers=auth(org_a),
        json={
            "email": staff_email,
            "full_name": "Dual Role",
            "role": "instructor",
            "password": PASSWORD,
        },
    )
    learner = (
        await client.post(
            "/api/v1/learners",
            headers=auth(org_a),
            json={"email": staff_email, "full_name": "Dual Role"},
        )
    ).json()

    response = await client.post(
        f"/api/v1/learners/{learner['id']}/access", headers=auth(org_a), json={}
    )
    assert response.status_code == 409
    assert "instructor" in response.json()["error"]["message"]


async def test_seed_creates_a_usable_learner_login(
    client: AsyncClient, sessionmaker_fixture, vector_store, monkeypatch
) -> None:
    """The demo must be able to show the learner side without a manual access grant."""
    from learnflow import db as db_module
    from learnflow.seed import DEMO_LEARNER_EMAIL, DEMO_PASSWORD, seed

    monkeypatch.setattr(db_module, "get_sessionmaker", lambda: sessionmaker_fixture)
    await seed()

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": DEMO_LEARNER_EMAIL, "password": DEMO_PASSWORD},
    )
    assert login.status_code == 200, login.text
    tokens = login.json()
    assert tokens["role"] == "learner"

    me = (await client.get("/api/v1/auth/me", headers=auth(tokens))).json()
    assert me["learner_id"], "the seeded learner login must resolve to its learner record"

    # And it is genuinely scoped, not just present.
    assert (await client.get("/api/v1/learners", headers=auth(tokens))).json()["total"] == 1
    assert (await client.get("/api/v1/analytics/overview", headers=auth(tokens))).status_code == 403
