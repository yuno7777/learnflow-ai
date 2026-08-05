"""Workflow execution, n8n hand-off/fallback, retries, webhook signing and replay defence."""

from __future__ import annotations

import json
import time

import pytest
from httpx import AsyncClient

from learnflow.automation import n8n_client
from learnflow.automation.email import get_email_adapter
from learnflow.security import sign_payload, verify_signature
from learnflow.worker.tasks import run_workflow

from .conftest import auth


async def _workflow(client: AsyncClient, tokens: dict, template: str, **overrides) -> dict:
    body = {
        "key": overrides.pop("key", template),
        "name": template.replace("_", " ").title(),
        "template": template,
        **overrides,
    }
    response = await client.post("/api/v1/workflows", headers=auth(tokens), json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------------ signing
def test_signature_roundtrip_and_tamper_detection() -> None:
    body = b'{"execution_id":"abc"}'
    header = sign_payload(body)
    assert verify_signature(body, header)

    from learnflow.errors import Unauthorized

    with pytest.raises(Unauthorized):
        verify_signature(b'{"execution_id":"tampered"}', header)
    with pytest.raises(Unauthorized):
        verify_signature(body, header, secret="a-different-secret")
    with pytest.raises(Unauthorized):
        verify_signature(body, "garbage")


def test_signature_outside_tolerance_is_rejected() -> None:
    from learnflow.errors import Unauthorized

    stale = sign_payload(b"{}", timestamp=int(time.time()) - 4000)
    with pytest.raises(Unauthorized):
        verify_signature(b"{}", stale)


# ------------------------------------------------------------------ execution
async def test_onboarding_workflow_runs_end_to_end(client: AsyncClient, org_a: dict) -> None:
    workflow = await _workflow(client, org_a, "learner_onboarding")
    course = (
        await client.post("/api/v1/courses", headers=auth(org_a), json={"title": "Induction"})
    ).json()

    learner = await client.post(
        "/api/v1/learners",
        headers=auth(org_a),
        json={
            "email": "newstarter@example.com",
            "full_name": "New Starter",
            "course_id": course["id"],
        },
    )
    assert learner.status_code == 201

    executions = (
        await client.get(f"/api/v1/executions?workflow_id={workflow['id']}", headers=auth(org_a))
    ).json()
    assert executions["total"] == 1, "creating a learner must trigger the onboarding workflow"
    execution = executions["items"][0]
    assert execution["status"] == "queued"
    assert execution["trigger_type"] == "event"

    # n8n is not running in tests; the built-in runner takes over and records the same steps.
    jobs = (await client.get("/api/v1/jobs?kind=run_workflow", headers=auth(org_a))).json()
    await run_workflow(
        {}, jobs["items"][0]["id"], org_a["organization_id"], execution_id=execution["id"]
    )

    detail = (await client.get(f"/api/v1/executions/{execution['id']}", headers=auth(org_a))).json()
    assert detail["status"] == "succeeded"
    assert detail["duration_ms"] is not None
    step_names = [s["name"] for s in detail["steps"]]
    assert step_names == [
        "validate_learner",
        "generate_welcome_message",
        "enrol_learner",
        "send_welcome_email",
        "create_onboarding_checklist",
        "schedule_reminders",
    ]
    assert all(s["status"] == "succeeded" for s in detail["steps"])
    assert detail["output_payload"]["welcome_message"]
    assert detail["input_tokens"] > 0, "AI spend must be attributed to the execution"

    outbox = get_email_adapter().outbox  # type: ignore[attr-defined]
    assert any(m.to == "newstarter@example.com" for m in outbox)

    learner_detail = (
        await client.get(f"/api/v1/learners/{learner.json()['id']}", headers=auth(org_a))
    ).json()
    assert learner_detail["status"] == "active"
    assert learner_detail["profile"]["onboarding_checklist"]
    assert learner_detail["enrollments"][0]["course_id"] == course["id"]


async def test_workflow_failure_is_recorded_with_error(client: AsyncClient, org_a: dict) -> None:
    workflow = await _workflow(client, org_a, "ticket_triage", key="triage-manual")
    triggered = await client.post(
        f"/api/v1/workflows/{workflow['id']}/trigger",
        headers=auth(org_a),
        json={"payload": {}},  # no ticket_id: the runner must fail loudly
    )
    execution = triggered.json()

    jobs = (await client.get("/api/v1/jobs?kind=run_workflow", headers=auth(org_a))).json()
    job_id = jobs["items"][0]["id"]

    with pytest.raises(RuntimeError):
        await run_workflow({}, job_id, org_a["organization_id"], execution_id=execution["id"])

    detail = (await client.get(f"/api/v1/executions/{execution['id']}", headers=auth(org_a))).json()
    assert detail["status"] == "failed"
    assert "ticket_id" in detail["error"]

    retried = await client.post(f"/api/v1/executions/{execution['id']}/retry", headers=auth(org_a))
    assert retried.status_code == 202
    assert retried.json()["retry_count"] == 1
    assert retried.json()["status"] == "queued"


async def test_trigger_is_idempotent(client: AsyncClient, org_a: dict) -> None:
    workflow = await _workflow(client, org_a, "weekly_summary")
    payload = {"payload": {}, "idempotency_key": "same-key"}
    first = await client.post(
        f"/api/v1/workflows/{workflow['id']}/trigger", headers=auth(org_a), json=payload
    )
    second = await client.post(
        f"/api/v1/workflows/{workflow['id']}/trigger", headers=auth(org_a), json=payload
    )
    assert first.json()["id"] == second.json()["id"]

    executions = (
        await client.get(f"/api/v1/executions?workflow_id={workflow['id']}", headers=auth(org_a))
    ).json()
    assert executions["total"] == 1


async def test_n8n_handoff_when_webhook_path_configured(
    client: AsyncClient, org_a: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    async def fake_trigger(path: str, payload: dict, **_kwargs) -> dict:
        captured["path"] = path
        captured["payload"] = payload
        return {"executionId": "n8n-4242"}

    monkeypatch.setattr(n8n_client, "trigger_webhook", fake_trigger)

    workflow = await _workflow(
        client, org_a, "weekly_summary", key="weekly-n8n", n8n_webhook_path="learnflow/weekly"
    )
    triggered = await client.post(
        f"/api/v1/workflows/{workflow['id']}/trigger",
        headers=auth(org_a),
        json={"payload": {"a": 1}},
    )
    execution = triggered.json()
    jobs = (await client.get("/api/v1/jobs?kind=run_workflow", headers=auth(org_a))).json()
    await run_workflow(
        {}, jobs["items"][0]["id"], org_a["organization_id"], execution_id=execution["id"]
    )

    assert captured["path"] == "learnflow/weekly"
    assert captured["payload"]["callback_url"].endswith("/webhooks/n8n/callback")
    assert captured["payload"]["data"] == {"a": 1}

    detail = (await client.get(f"/api/v1/executions/{execution['id']}", headers=auth(org_a))).json()
    assert detail["n8n_execution_id"] == "n8n-4242"
    assert detail["status"] == "running", "the run stays open until n8n calls back"


async def test_n8n_callback_closes_execution(client: AsyncClient, org_a: dict) -> None:
    workflow = await _workflow(client, org_a, "weekly_summary", key="weekly-callback")
    execution = (
        await client.post(
            f"/api/v1/workflows/{workflow['id']}/trigger", headers=auth(org_a), json={"payload": {}}
        )
    ).json()

    body = json.dumps(
        {
            "execution_id": execution["id"],
            "status": "succeeded",
            "n8n_execution_id": "n8n-99",
            "output": {"emails_sent": 4},
            "steps": [
                {
                    "name": "n8n:send_digest",
                    "kind": "email",
                    "status": "succeeded",
                    "output": {"recipients": 4},
                    "duration_ms": 120,
                }
            ],
            "input_tokens": 100,
            "output_tokens": 50,
        }
    ).encode()
    signature = sign_payload(body)

    response = await client.post(
        "/api/v1/webhooks/n8n/callback",
        content=body,
        headers={"Content-Type": "application/json", "X-LearnFlow-Signature": signature},
    )
    assert response.status_code == 202

    detail = (await client.get(f"/api/v1/executions/{execution['id']}", headers=auth(org_a))).json()
    assert detail["status"] == "succeeded"
    assert detail["n8n_execution_id"] == "n8n-99"
    assert detail["output_payload"]["emails_sent"] == 4
    assert detail["steps"][-1]["name"] == "n8n:send_digest"
    assert detail["input_tokens"] == 100

    # Replaying the exact same signed body must not double-apply.
    replay = await client.post(
        "/api/v1/webhooks/n8n/callback",
        content=body,
        headers={"Content-Type": "application/json", "X-LearnFlow-Signature": signature},
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["message"] == "Replayed webhook signature"


async def test_callback_without_signature_is_rejected(client: AsyncClient, org_a: dict) -> None:
    response = await client.post(
        "/api/v1/webhooks/n8n/callback",
        json={"execution_id": "00000000-0000-0000-0000-000000000000", "status": "succeeded"},
    )
    assert response.status_code == 401


async def test_inbound_event_webhook_dispatches_workflow(client: AsyncClient, org_a: dict) -> None:
    await _workflow(
        client, org_a, "weekly_summary", key="external-hook", trigger_event="external.ping"
    )
    slug = (await client.get("/api/v1/organizations/current", headers=auth(org_a))).json()["slug"]

    body = json.dumps({"source": "hris"}).encode()
    response = await client.post(
        f"/api/v1/webhooks/events/{slug}/external.ping",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-LearnFlow-Signature": sign_payload(body),
            "X-LearnFlow-Event-Id": "hris-1",
        },
    )
    assert response.status_code == 202
    assert len(response.json()["executions"]) == 1


async def test_ticket_triage_workflow_classifies_and_escalates(
    client: AsyncClient, org_a: dict
) -> None:
    await _workflow(client, org_a, "ticket_triage", trigger_event="ticket.created")
    ticket = (
        await client.post(
            "/api/v1/tickets",
            headers=auth(org_a),
            json={
                "subject": "I cannot access my course",
                "body": "The remote work module will not open and I have a deadline.",
            },
        )
    ).json()

    jobs = (await client.get("/api/v1/jobs?kind=run_workflow", headers=auth(org_a))).json()
    executions = (await client.get("/api/v1/executions", headers=auth(org_a))).json()
    await run_workflow(
        {},
        jobs["items"][0]["id"],
        org_a["organization_id"],
        execution_id=executions["items"][0]["id"],
    )

    detail = (await client.get(f"/api/v1/tickets/{ticket['id']}", headers=auth(org_a))).json()
    assert detail["category"] in {
        "course_access",
        "payment_issue",
        "technical_problem",
        "assessment_query",
        "certificate_issue",
        "general_learning",
    }
    assert detail["urgency"] and detail["sentiment"]
    assert detail["ai_confidence"] is not None
    assert detail["ai_explanation"]
    assert detail["status"] in {"triaged", "escalated"}


async def test_workflow_analytics_reflect_runs(client: AsyncClient, org_a: dict) -> None:
    workflow = await _workflow(client, org_a, "weekly_summary", key="stats-flow")
    execution = (
        await client.post(
            f"/api/v1/workflows/{workflow['id']}/trigger", headers=auth(org_a), json={"payload": {}}
        )
    ).json()
    jobs = (await client.get("/api/v1/jobs?kind=run_workflow", headers=auth(org_a))).json()
    await run_workflow(
        {}, jobs["items"][0]["id"], org_a["organization_id"], execution_id=execution["id"]
    )

    stats = (await client.get("/api/v1/analytics/workflows", headers=auth(org_a))).json()
    row = next(r for r in stats if r["key"] == "stats-flow")
    assert row["total"] == 1
    assert row["succeeded"] == 1
    assert row["success_rate"] == 1.0
    assert row["last_run_at"]
