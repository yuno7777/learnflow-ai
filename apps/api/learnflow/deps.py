"""Request-scoped dependencies: principal resolution, tenant context, and RBAC authorization.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file is the security and context hub for all FastAPI route handlers.
It defines:
1. `Principal`: A unified dataclass representing the caller (either a human User
   with a JWT or an external machine using an API Key).
2. `get_principal`: The core authentication dependency that inspects incoming
   headers, verifies JWTs or API Key hashes, and loads tenant membership.
3. `scoped_learner_id`: An identity isolation function ensuring that learners can
   ONLY ever access their own learner record (returning 404 for anyone else).
4. `get_repo` / `RepoDep`: Dependency that constructs a `TenantRepo` bound
   strictly to the caller's `organization_id`.
5. `require(*permissions)`: An RBAC route guard function checking if the caller's
   role possesses the required capabilities.

===============================================================================
WHY THIS FILE EXISTS & WHAT HAPPENS IF REMOVED:
===============================================================================
If this file were removed:
- Endpoints would have no way to authenticate incoming requests.
- Multi-tenancy would fail because routes wouldn't know which organization the
  caller belongs to.
- Role-based authorization and learner data isolation would completely collapse.

===============================================================================
REQUEST EXECUTION CHAIN:
===============================================================================
Client Request (with Authorization Header or X-API-Key)
  ──► FastAPI Route (`routers/*.py`)
  ──► Depends(`get_principal`)
  ──► Validates JWT/API Key against PostgreSQL (`users` / `api_keys`)
  ──► Verifies Organization status (`organizations`)
  ──► Returns `Principal(user, org_id, role, learner_id)`
  ──► Depends(`require("course:write")`) [Checks ROLE_PERMISSIONS]
  ──► Depends(`get_repo`) [Constructs `TenantRepo(session, principal.organization_id)`]
  ──► Route handler executes safely within tenant boundary.
"""

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

# FastAPI HTTPBearer security scheme helper (auto_error=False allows custom fallbacks to API keys)
bearer = HTTPBearer(auto_error=False)

# Annotated type alias for injecting async database sessions into route handlers
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@dataclass
class Principal:
    """Represents the authenticated entity making the current API request.

    Can be:
    1. A human `User` logged in via JWT access token.
    2. An automated integration holding an `APIKey` (`lf_...`).

    Attributes:
        organization_id: The UUID of the tenant organization this request is scoped to.
        role: The caller's assigned role (e.g. `org_admin`, `instructor`, `learner`).
        user: The SQLAlchemy User instance (if logged in via JWT).
        api_key: The SQLAlchemy APIKey instance (if authenticated via X-API-Key).
        learner_id: If the role is `learner`, this holds the specific `learners.id` record
                    linked to this user account, preventing cross-student data access.
    """

    organization_id: uuid.UUID
    role: Role
    user: User | None = None
    api_key: APIKey | None = None
    learner_id: uuid.UUID | None = None

    @property
    def is_learner(self) -> bool:
        """Helper to quickly check if the principal is a student/learner."""
        return self.role is Role.learner

    @property
    def actor_type(self) -> str:
        """Returns 'user' or 'api_key' for audit logging."""
        return "user" if self.user else "api_key"

    @property
    def actor_id(self) -> str:
        """Returns the string UUID of the user or API key."""
        return str(self.user.id if self.user else self.api_key.id)  # type: ignore[union-attr]

    @property
    def actor_label(self) -> str:
        """Human-readable identifier for audit logs (user email or API key prefix)."""
        return self.user.email if self.user else f"api_key:{self.api_key.prefix}"  # type: ignore[union-attr]

    def has(self, permission: str) -> bool:
        """Checks if the caller's role is granted the requested permission string.

        Consults the `ROLE_PERMISSIONS` dictionary defined in `learnflow/models/tenancy.py`.
        """
        return self.role in ROLE_PERMISSIONS.get(permission, set())


async def _principal_from_jwt(
    token: str, session: AsyncSession, requested_org: uuid.UUID | None
) -> Principal:
    """Decodes a JWT access token and resolves the user's role within an organization.

    Security flow:
    1. Decodes and validates signature + expiration using `security.decode_token()`.
    2. Loads user from PostgreSQL `users` table and ensures `is_active=True`.
    3. Resolves the target organization ID:
       - Uses `X-Organization-Id` header if supplied (for multi-org users).
       - Otherwise falls back to the default `org` claim inside the JWT.
    4. Queries `organization_members` table to verify membership and role.
       - Note: Roles are checked against the database on EVERY request, not trusted
         blindly from the token claim. This allows immediate role revocation!
    """
    payload = decode_token(token, "access")
    user = await session.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise Unauthorized("User is inactive or missing")

    # Determine which organization context the user wants to act on
    org_id = requested_org or (uuid.UUID(payload["org"]) if payload.get("org") else None)
    if org_id is None:
        raise Forbidden("No organization selected; pass X-Organization-Id")

    # Check the database for active membership
    membership = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.user_id == user.id,
            OrganizationMember.organization_id == org_id,
        )
    )
    if membership is None:
        # Platform admins (superusers) can access any organization as platform_admin
        if not user.is_platform_admin:
            raise Forbidden("You are not a member of this organization")
        return Principal(organization_id=org_id, role=Role.platform_admin, user=user)
    return Principal(organization_id=org_id, role=membership.role, user=user)


async def _principal_from_api_key(key: str, session: AsyncSession) -> Principal:
    """Authenticates machine-to-machine requests using an API key (`X-API-Key: lf_...`).

    Security flow:
    1. Computes SHA-256 hash of the raw provided key (`security.sha256_hex`).
    2. Queries `api_keys` table matching `key_hash`.
       (Raw API keys are never stored in plaintext in the database).
    3. Verifies that `revoked_at` is None and `expires_at` has not passed.
    4. Updates `last_used_at` timestamp for usage telemetry.
    5. Grants `Role.org_admin` within the key's parent organization.
    """
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
    """Primary FastAPI Dependency for authenticating requests.

    Supports two authentication mechanisms:
    1. Header: `X-API-Key: lf_...` (for automated systems/n8n)
    2. Header: `Authorization: Bearer <JWT>` (for interactive web users)

    After resolving the principal:
    - Verifies that the organization is active in PostgreSQL.
    - If the user is a `learner`, resolves their linked `learners.id` record.
    - Attaches the principal to `request.state.principal` for downstream logging.
    """
    if x_api_key:
        principal = await _principal_from_api_key(x_api_key, session)
    elif credentials and credentials.scheme.lower() == "bearer":
        principal = await _principal_from_jwt(credentials.credentials, session, x_organization_id)
    else:
        raise Unauthorized("Missing credentials")

    # Ensure the parent organization exists and has not been deactivated
    org = await session.get(Organization, principal.organization_id)
    if org is None or not org.is_active:
        raise Forbidden("Organization is inactive")

    # If this login has the 'learner' role, link it to their learner record
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
    """Resolves which learner an action may target, strictly enforcing isolation.

    Rules:
    - Staff roles (Admin, Instructor, Support) can query any requested learner in the org.
    - A Learner role can ONLY ever query themselves (`principal.learner_id`).
    - If a learner tries to query another student's ID, this raises `NotFound("Learner not found")`
      (a 404, NOT a 403!). This prevents malicious learners from probing whether other IDs exist.
    """
    if not principal.is_learner:
        return requested
    if principal.learner_id is None:
        raise Forbidden("This login is not linked to a learner record")
    if requested is not None and requested != principal.learner_id:
        raise NotFound("Learner not found")
    return principal.learner_id


# Annotated dependency alias for injecting the authenticated Principal into endpoints
PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def get_repo(principal: PrincipalDep, session: SessionDep) -> TenantRepo:
    """Constructs a `TenantRepo` bound to the caller's organization.

    Every database query made through this repository automatically filters by
    `organization_id == principal.organization_id`.
    """
    return TenantRepo(session, principal.organization_id)


# Annotated dependency alias for injecting the TenantRepo into endpoints
RepoDep = Annotated[TenantRepo, Depends(get_repo)]


def require(*permissions: str):
    """Route Guard Factory: enforces RBAC permissions on endpoints.

    Usage in routers:
        @router.post("/courses", dependencies=[Depends(require("course:write"))])
        async def create_course(...):

    If the caller's role lacks any of the required permissions, raises `403 Forbidden`.
    """

    async def _check(principal: PrincipalDep) -> Principal:
        missing = [p for p in permissions if not principal.has(p)]
        if missing:
            raise Forbidden(f"Role '{principal.role.value}' lacks permission: {', '.join(missing)}")
        return principal

    return _check
