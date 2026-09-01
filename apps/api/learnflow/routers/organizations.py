"""Organization tenancy, staff memberships, and workspace management API router.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file manages tenant settings, team members, and logical workspaces:
1. `GET /organizations/current` & `PATCH /current`: Inspect and update tenant name/slug/config.
2. `GET /organizations/current/members`: Lists staff members and their assigned RBAC roles.
3. `POST /organizations/current/members`: Invites/attaches a user to the organization.
4. `PATCH /current/members/{id}`: Changes member role (with safety guard preventing self-demotion).
5. `DELETE /current/members/{id}`: Removes staff member (with safety guard preventing self-removal).
6. Workspaces (`/workspaces`): Creates and lists sub-organizational partitions.

===============================================================================
ROLE-BASED ACCESS CONTROL (RBAC):
===============================================================================
- Endpoints enforce `require("org:manage")` and `require("member:manage")` dependencies.
- Prevents privilege escalation and maintains multi-tenant boundary integrity.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select

from ..deps import PrincipalDep, RepoDep, SessionDep, require
from ..errors import Conflict, Forbidden, NotFound
from ..models import Organization, OrganizationMember, User, Workspace
from ..schemas.common import Page, PageParams, page_params
from ..schemas.tenancy import (
    MemberInvite,
    MemberOut,
    MemberRoleUpdate,
    OrganizationOut,
    OrganizationUpdate,
    WorkspaceCreate,
    WorkspaceOut,
)
from ..security import hash_password
from ..services import audit

router = APIRouter(prefix="/organizations", tags=["organizations"])
workspaces = APIRouter(prefix="/workspaces", tags=["organizations"])


# -----------------------------------------------------------------------------
# Organization & Member Endpoints
# -----------------------------------------------------------------------------

@router.get("/current", response_model=OrganizationOut, summary="The caller's organization")
async def current(principal: PrincipalDep, session: SessionDep) -> Organization:
    """Returns metadata for the active caller's organization tenant."""
    org = await session.get(Organization, principal.organization_id)
    if org is None:
        raise NotFound("Organization not found")
    return org


@router.patch(
    "/current",
    response_model=OrganizationOut,
    dependencies=[Depends(require("org:manage"))],
    summary="Update organization settings",
)
async def update(
    body: OrganizationUpdate, principal: PrincipalDep, session: SessionDep, request: Request
) -> Organization:
    """Updates organization name or default configurations."""
    org = await session.get(Organization, principal.organization_id)
    if org is None:
        raise NotFound("Organization not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(org, field, value)
    await audit.record(
        session, principal, "organization.update", "organization", str(org.id), request=request
    )
    return org


@router.get("/current/members", response_model=Page[MemberOut], summary="List members")
async def list_members(
    principal: PrincipalDep, session: SessionDep, params: PageParams = Depends(page_params)
) -> Page[MemberOut]:
    """Lists all staff members belonging to the current organization."""
    stmt = (
        select(OrganizationMember, User)
        .join(User, User.id == OrganizationMember.user_id)
        .where(OrganizationMember.organization_id == principal.organization_id)
    )
    rows = (await session.execute(stmt.limit(params.limit).offset(params.offset))).all()
    total = len((await session.execute(stmt)).all())
    return Page(
        items=[
            MemberOut(
                id=member.id,
                user_id=user.id,
                email=user.email,
                full_name=user.full_name,
                role=member.role,
                created_at=member.created_at,
            )
            for member, user in rows
        ],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.post(
    "/current/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require("member:manage"))],
    summary="Invite or attach a user to this organization",
)
async def add_member(
    body: MemberInvite, principal: PrincipalDep, session: SessionDep, request: Request
) -> MemberOut:
    """Invites a user into the organization with a designated role."""
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        user = User(
            email=body.email.lower(),
            full_name=body.full_name,
            password_hash=hash_password(body.password or uuid.uuid4().hex + "aA1!"),
            is_active=True,
        )
        session.add(user)
        await session.flush()

    existing = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == principal.organization_id,
            OrganizationMember.user_id == user.id,
        )
    )
    if existing is not None:
        raise Conflict("User is already a member of this organization")

    member = OrganizationMember(
        organization_id=principal.organization_id, user_id=user.id, role=body.role
    )
    session.add(member)
    await session.flush()
    await audit.record(
        session,
        principal,
        "member.add",
        "organization_member",
        str(member.id),
        request=request,
        details={"email": user.email, "role": body.role.value},
    )
    return MemberOut(
        id=member.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=member.role,
        created_at=member.created_at,
    )


@router.patch(
    "/current/members/{member_id}",
    response_model=MemberOut,
    dependencies=[Depends(require("member:manage"))],
    summary="Change a member's role",
)
async def update_member(
    member_id: uuid.UUID,
    body: MemberRoleUpdate,
    principal: PrincipalDep,
    session: SessionDep,
    request: Request,
) -> MemberOut:
    """Updates a member's RBAC role (prevents changing caller's own role)."""
    member = await session.get(OrganizationMember, member_id)
    if member is None or member.organization_id != principal.organization_id:
        raise NotFound("Member not found")
    if member.user_id == (principal.user.id if principal.user else None):
        raise Forbidden("You cannot change your own role")
    member.role = body.role
    user = await session.get(User, member.user_id)
    await audit.record(
        session,
        principal,
        "member.role_change",
        "organization_member",
        str(member.id),
        request=request,
        details={"role": body.role.value},
    )
    return MemberOut(
        id=member.id,
        user_id=member.user_id,
        email=user.email if user else "",
        full_name=user.full_name if user else "",
        role=member.role,
        created_at=member.created_at,
    )


@router.delete(
    "/current/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require("member:manage"))],
    summary="Remove a member",
)
async def remove_member(
    member_id: uuid.UUID, principal: PrincipalDep, session: SessionDep, request: Request
) -> None:
    """Removes a user from the organization (prevents removing yourself)."""
    member = await session.get(OrganizationMember, member_id)
    if member is None or member.organization_id != principal.organization_id:
        raise NotFound("Member not found")
    if member.user_id == (principal.user.id if principal.user else None):
        raise Forbidden("You cannot remove yourself")
    await session.delete(member)
    await audit.record(
        session, principal, "member.remove", "organization_member", str(member_id), request=request
    )


# -----------------------------------------------------------------------------
# Workspace Endpoints
# -----------------------------------------------------------------------------

@workspaces.get("", response_model=Page[WorkspaceOut], summary="List workspaces")
async def list_workspaces(
    repo: RepoDep, params: PageParams = Depends(page_params)
) -> Page[WorkspaceOut]:
    """Lists sub-organization workspace partitions."""
    items, total = await repo.list(
        Workspace, sort=params.sort, limit=params.limit, offset=params.offset
    )
    return Page(
        items=[WorkspaceOut.model_validate(w) for w in items],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@workspaces.post(
    "",
    response_model=WorkspaceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require("org:manage"))],
    summary="Create a workspace",
)
async def create_workspace(
    body: WorkspaceCreate,
    repo: RepoDep,
    principal: PrincipalDep,
    session: SessionDep,
    request: Request,
) -> Workspace:
    """Creates a new workspace partition in the current tenant."""
    workspace = repo.add(Workspace(**body.model_dump()))
    await repo.flush()
    await audit.record(
        session, principal, "workspace.create", "workspace", str(workspace.id), request=request
    )
    return workspace
