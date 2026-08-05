"""Authentication, RBAC and the property everything else depends on: tenant isolation."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from learnflow.errors import TenantIsolationError
from learnflow.models import Learner, Organization, Role
from learnflow.services.repository import TenantRepo

from .conftest import auth


async def test_register_creates_org_admin(org_a: dict) -> None:
    assert org_a["role"] == Role.org_admin.value
    assert org_a["memberships"][0]["organization_name"] == "Acme Training"
    assert "password" not in str(org_a)


async def test_login_rejects_wrong_password(client: AsyncClient, org_a: dict) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": org_a["user"]["email"], "password": "wrong-password-here"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_login_does_not_leak_account_existence(client: AsyncClient, org_a: dict) -> None:
    missing = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever123!"}
    )
    wrong = await client.post(
        "/api/v1/auth/login", json={"email": org_a["user"]["email"], "password": "whatever123!"}
    )
    assert missing.json()["error"]["message"] == wrong.json()["error"]["message"]


async def test_refresh_token_is_single_use(client: AsyncClient, org_a: dict) -> None:
    first = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": org_a["refresh_token"]}
    )
    assert first.status_code == 200
    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": org_a["refresh_token"]}
    )
    assert replay.status_code == 401


async def test_me_reports_permissions(client: AsyncClient, org_a: dict) -> None:
    response = await client.get("/api/v1/auth/me", headers=auth(org_a))
    assert response.status_code == 200
    assert "course:write" in response.json()["permissions"]


async def test_unauthenticated_request_is_rejected(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/learners")).status_code == 401


async def test_learner_role_cannot_manage_learners(client: AsyncClient, org_a: dict) -> None:
    invite = await client.post(
        "/api/v1/organizations/current/members",
        headers=auth(org_a),
        json={
            "email": "student@example.com",
            "full_name": "Student One",
            "role": "learner",
            "password": "Sup3rSecret!pass",
        },
    )
    assert invite.status_code == 201

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "student@example.com", "password": "Sup3rSecret!pass"},
    )
    student = login.json()
    forbidden = await client.post(
        "/api/v1/learners",
        headers=auth(student),
        json={"email": "x@example.com", "full_name": "X Y"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"


async def test_cross_tenant_read_returns_404(client: AsyncClient, org_a: dict, org_b: dict) -> None:
    created = await client.post(
        "/api/v1/learners",
        headers=auth(org_a),
        json={"email": "learner@acme-training.example.com", "full_name": "Acme Learner"},
    )
    assert created.status_code == 201
    learner_id = created.json()["id"]

    # Same id, other tenant's token: must be indistinguishable from "does not exist".
    leaked = await client.get(f"/api/v1/learners/{learner_id}", headers=auth(org_b))
    assert leaked.status_code == 404

    listing = await client.get("/api/v1/learners", headers=auth(org_b))
    assert listing.json()["total"] == 0


async def test_repository_refuses_untenanted_model(session) -> None:
    repo = TenantRepo(session, uuid_of := __import__("uuid").uuid4())
    assert repo.organization_id == uuid_of
    with pytest.raises(TenantIsolationError):
        repo.select(Organization)


async def test_repository_stamps_tenant_on_write(session) -> None:
    import uuid

    org_id = uuid.uuid4()
    repo = TenantRepo(session, org_id)
    learner = repo.add(Learner(email="a@b.c", full_name="A B", organization_id=uuid.uuid4()))
    assert learner.organization_id == org_id


async def test_api_key_authenticates_and_is_scoped(client: AsyncClient, org_a: dict) -> None:
    created = await client.post(
        "/api/v1/api-keys", headers=auth(org_a), json={"name": "ci", "scopes": ["read"]}
    )
    assert created.status_code == 201
    raw = created.json()["key"]
    assert raw.startswith("lf_")

    listed = await client.get("/api/v1/api-keys", headers=auth(org_a))
    assert "key" not in listed.json()["items"][0]  # raw value never returned again

    response = await client.get("/api/v1/learners", headers={"X-API-Key": raw})
    assert response.status_code == 200

    revoked = await client.delete(f"/api/v1/api-keys/{created.json()['id']}", headers=auth(org_a))
    assert revoked.status_code == 204
    assert (await client.get("/api/v1/learners", headers={"X-API-Key": raw})).status_code == 401
