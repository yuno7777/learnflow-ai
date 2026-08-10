"""Request-scoped dependencies: principal resolution, tenant context, RBAC."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .errors import Forbidden, NotFound, Unauthorized
from .models import (
    ROLE_PERMISSIONS,
    APIKey,
    Learner,
    Organization,
    OrganizationMember,
    Role,
    User,
    as_utc,
)
from .security import decode_token, sha256_hex
from .services.repository import TenantRepo

bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@dataclass
class Principal:
    """Whoever is making this request: a logged-in user or a machine holding an API key."""

    organization_id: uuid.UUID
    role: Role
    user: User | None = None
    api_key: APIKey | None = None
    # Set only for the learner role: the Learner record this login speaks for. Everything a
    # learner can reach is scoped to it.
    learner_id: uuid.UUID | None = None

    @property
    def is_learner(self) -> bool:
        return self.role is Role.learner

    @property
    def actor_type(self) -> str:
        return "user" if self.user else "api_key"

    @property
    def actor_id(self) -> str:
        return str(self.user.id if self.user else self.api_key.id)  # type: ignore[union-attr]

    @property
    def actor_label(self) -> str:
        return self.user.email if self.user else f"api_key:{self.api_key.prefix}"  # type: ignore[union-attr]

    def has(self, permission: str) -> bool:
        return self.role in ROLE_PERMISSIONS.get(permission, set())


async def _principal_from_jwt(
    token: str, session: AsyncSession, requested_org: uuid.UUID | None
) -> Principal:
    payload = decode_token(token, "access")
    user = await session.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise Unauthorized("User is inactive or missing")

    org_id = requested_org or (uuid.UUID(payload["org"]) if payload.get("org") else None)
    if org_id is None:
        raise Forbidden("No organization selected; pass X-Organization-Id")

    membership = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.user_id == user.id,
            OrganizationMember.organization_id == org_id,
        )
    )
    if membership is None:
        if not user.is_platform_admin:
            raise Forbidden("You are not a member of this organization")
        return Principal(organization_id=org_id, role=Role.platform_admin, user=user)
    return Principal(organization_id=org_id, role=membership.role, user=user)


async def _principal_from_api_key(key: str, session: AsyncSession) -> Principal:
    record = await session.scalar(select(APIKey).where(APIKey.key_hash == sha256_hex(key)))
    now = datetime.now(UTC)
    if record is None or record.revoked_at is not None:
        raise Unauthorized("Invalid API key")
    if (expires := as_utc(record.expires_at)) and expires < now:
        raise Unauthorized("API key expired")
    record.last_used_at = now
    return Principal(organization_id=record.organization_id, role=Role.org_admin, api_key=record)


async def get_principal(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    x_organization_id: Annotated[uuid.UUID | None, Header(alias="X-Organization-Id")] = None,
) -> Principal:
    if x_api_key:
        principal = await _principal_from_api_key(x_api_key, session)
    elif credentials and credentials.scheme.lower() == "bearer":
        principal = await _principal_from_jwt(credentials.credentials, session, x_organization_id)
    else:
        raise Unauthorized("Missing credentials")

    org = await session.get(Organization, principal.organization_id)
    if org is None or not org.is_active:
        raise Forbidden("Organization is inactive")

    if principal.is_learner and principal.user is not None:
        principal.learner_id = await session.scalar(
            select(Learner.id).where(
                Learner.user_id == principal.user.id,
                Learner.organization_id == principal.organization_id,
            )
        )

    request.state.principal = principal
    return principal


def scoped_learner_id(principal: Principal, requested: uuid.UUID | None) -> uuid.UUID | None:
    """Resolve which learner an action may target.

    Staff roles act on whoever they name. A learner may only ever act on itself, and asking
    for another learner returns the same 404 a nonexistent id would - consistent with how
    cross-tenant reads behave, so ids cannot be probed.
    """
    if not principal.is_learner:
        return requested
    if principal.learner_id is None:
        raise Forbidden("This login is not linked to a learner record")
    if requested is not None and requested != principal.learner_id:
        raise NotFound("Learner not found")
    return principal.learner_id


PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def get_repo(principal: PrincipalDep, session: SessionDep) -> TenantRepo:
    return TenantRepo(session, principal.organization_id)


RepoDep = Annotated[TenantRepo, Depends(get_repo)]


def require(*permissions: str):
    """Route guard: `dependencies=[Depends(require("course:write"))]`."""

    async def _check(principal: PrincipalDep) -> Principal:
        missing = [p for p in permissions if not principal.has(p)]
        if missing:
            raise Forbidden(f"Role '{principal.role.value}' lacks permission: {', '.join(missing)}")
        return principal

    return _check
