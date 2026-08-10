"""Seed data must actually load, and analytics must report it (not fabricate it)."""

from __future__ import annotations

from httpx import AsyncClient

from learnflow import db as db_module
from learnflow.seed import DEMO_PASSWORD, ORG_SLUG, seed

from .conftest import auth


async def test_seed_populates_a_working_organization(
    client: AsyncClient, sessionmaker_fixture, vector_store, monkeypatch
) -> None:
    monkeypatch.setattr(db_module, "get_sessionmaker", lambda: sessionmaker_fixture)
    await seed()

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@learnflow.dev", "password": DEMO_PASSWORD},
    )
    assert login.status_code == 200, login.text
    tokens = login.json()
    assert tokens["memberships"][0]["organization_slug"] == ORG_SLUG

    learners = (await client.get("/api/v1/learners?limit=100", headers=auth(tokens))).json()
    assert learners["total"] == 10

    courses = (await client.get("/api/v1/courses", headers=auth(tokens))).json()
    assert courses["total"] == 3

    documents = (await client.get("/api/v1/documents", headers=auth(tokens))).json()
    assert documents["total"] == 3
    assert all(d["status"] == "indexed" for d in documents["items"])

    tickets = (await client.get("/api/v1/tickets", headers=auth(tokens))).json()
    assert tickets["total"] == 5

    executions = (await client.get("/api/v1/executions", headers=auth(tokens))).json()
    assert executions["total"] == 18
    assert any(e["status"] == "failed" for e in executions["items"])

    queue = (await client.get("/api/v1/evaluations/queue", headers=auth(tokens))).json()
    assert queue, "some seeded evaluations must be waiting for a human"


async def test_seed_is_idempotent(
    client: AsyncClient, sessionmaker_fixture, vector_store, monkeypatch
) -> None:
    monkeypatch.setattr(db_module, "get_sessionmaker", lambda: sessionmaker_fixture)
    await seed()
    await seed()

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@learnflow.dev", "password": DEMO_PASSWORD},
    )
    tokens = login.json()
    learners = (await client.get("/api/v1/learners?limit=100", headers=auth(tokens))).json()
    assert learners["total"] == 10, "re-seeding must not duplicate rows"


async def test_analytics_report_real_numbers(
    client: AsyncClient, sessionmaker_fixture, vector_store, monkeypatch
) -> None:
    monkeypatch.setattr(db_module, "get_sessionmaker", lambda: sessionmaker_fixture)
    await seed()
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@learnflow.dev", "password": DEMO_PASSWORD},
    )
    tokens = login.json()

    overview = (await client.get("/api/v1/analytics/overview", headers=auth(tokens))).json()
    assert overview["total_learners"] == 10
    assert 0 < overview["active_learners"] <= 10
    assert overview["documents_indexed"] == 3
    assert overview["ai_requests"] > 0
    assert overview["tokens_consumed"] > 0
    assert overview["estimated_cost_usd"] > 0
    assert 0 <= overview["workflow_success_rate"] <= 1
    assert overview["workflow_failure_rate"] > 0, "seeded failures must be visible"
    assert overview["average_workflow_duration_ms"] > 0

    usage = (await client.get("/api/v1/analytics/usage?days=60", headers=auth(tokens))).json()
    assert usage["total_cost_usd"] > 0
    assert usage["by_model"], "cost must break down per model"
    assert usage["by_operation"], "cost must break down per operation"
    assert any(row["failures"] > 0 for row in usage["by_operation"])
    assert usage["daily"], "there must be a daily series to chart"

    series = (
        await client.get("/api/v1/analytics/timeseries?metric=cost&days=30", headers=auth(tokens))
    ).json()
    assert series["metric"] == "cost"
    assert len(series["points"]) > 5

    inactive = (
        await client.get(
            "/api/v1/analytics/inactive-learners?threshold_days=14", headers=auth(tokens)
        )
    ).json()
    assert inactive["threshold_days"] == 14
    assert inactive["learners"], "the seed includes learners past the threshold"
    assert all(
        item["days_inactive"] is None or item["days_inactive"] >= 14
        for item in inactive["learners"]
    )


async def test_empty_org_reports_zeros_not_fake_data(client: AsyncClient, org_a: dict) -> None:
    overview = (await client.get("/api/v1/analytics/overview", headers=auth(org_a))).json()
    assert overview["total_learners"] == 0
    assert overview["ai_requests"] == 0
    assert overview["estimated_cost_usd"] == 0
    assert overview["course_completion_rate"] == 0.0
    assert overview["average_assessment_score"] is None
    assert overview["llm_response_latency_ms"] is None


async def test_audit_log_records_privileged_actions(client: AsyncClient, org_a: dict) -> None:
    await client.post(
        "/api/v1/learners",
        headers=auth(org_a),
        json={"email": "audited@example.com", "full_name": "Audited Learner"},
    )
    logs = (await client.get("/api/v1/audit-logs", headers=auth(org_a))).json()
    actions = [entry["action"] for entry in logs["items"]]
    assert "learner.create" in actions
    assert "auth.register" in actions
    entry = next(e for e in logs["items"] if e["action"] == "learner.create")
    assert entry["actor_type"] == "user"
    assert entry["request_id"]
    assert "password" not in str(entry).lower()


async def test_seeded_usage_is_distinguishable_from_real_spend(
    client: AsyncClient, sessionmaker_fixture, vector_store, monkeypatch
) -> None:
    """The cost dashboard must not present fabricated history as real model spend."""
    monkeypatch.setattr(db_module, "get_sessionmaker", lambda: sessionmaker_fixture)
    await seed()
    tokens = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@learnflow.dev", "password": DEMO_PASSWORD},
        )
    ).json()

    with_demo = (await client.get("/api/v1/analytics/usage?days=60", headers=auth(tokens))).json()
    assert with_demo["includes_demo_data"] is True
    assert with_demo["demo_requests"] > 0
    assert with_demo["total_cost_usd"] > 0

    real_only = (
        await client.get("/api/v1/analytics/usage?days=60&exclude_demo=true", headers=auth(tokens))
    ).json()
    assert real_only["includes_demo_data"] is False
    assert real_only["total_requests"] < with_demo["total_requests"], (
        "excluding seeded rows must actually change the figures"
    )

    overview = (await client.get("/api/v1/analytics/overview", headers=auth(tokens))).json()
    assert overview["includes_demo_data"] is True
    clean = (
        await client.get("/api/v1/analytics/overview?exclude_demo=true", headers=auth(tokens))
    ).json()
    assert clean["includes_demo_data"] is False
    assert clean["ai_requests"] < overview["ai_requests"]

    # Embeddings written by real ingestion during the seed are not marked as demo data.
    assert real_only["total_requests"] > 0, "genuine calls should survive the filter"
