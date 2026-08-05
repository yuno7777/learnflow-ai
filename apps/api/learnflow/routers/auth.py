"""Email + password auth issuing short-lived JWTs and rotating refresh tokens.

Flow: `POST /auth/register` creates the user, their organization and an org_admin membership.
`POST /auth/login` returns an access token (30 min) plus a refresh token stored only as a
SHA-256 hash. `POST /auth/refresh` rotates: the presented token is revoked as it is redeemed,
so a replayed refresh token fails.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from ..deps import PrincipalDep, SessionDep
from ..errors import Conflict, Unauthorized
from ..models import (
    ROLE_PERMISSIONS,
    Organization,
    OrganizationMember,
    RefreshToken,
    Role,
    User,
    as_utc,
)
from ..schemas.auth import (
    LoginRequest,
    MembershipOut,
    MeOut,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
)
from ..security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    sha256_hex,
    verify_password,
)
from ..services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:60] or "org"


async def _memberships(session, user: User) -> list[MembershipOut]:
    rows = (
        await session.execute(
            select(OrganizationMember, Organization)
            .join(Organization, Organization.id == OrganizationMember.organization_id)
            .where(OrganizationMember.user_id == user.id)
        )
    ).all()
    return [
        MembershipOut(
            organization_id=org.id,
            organization_name=org.name,
            organization_slug=org.slug,
            role=member.role,
        )
        for member, org in rows
    ]


async def _issue(session, user: User, organization_id: uuid.UUID, role: Role, request: Request):
    access, expires_in = create_access_token(user.id, organization_id, role.value)
    raw_refresh, token_hash, expires_at = create_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=request.headers.get("user-agent", "")[:300],
        )
    )
    user.last_login_at = datetime.now(UTC)
    return TokenPair(
        access_token=access,
        refresh_token=raw_refresh,
        expires_in=expires_in,
        user=UserOut.model_validate(user),
        organization_id=organization_id,
        role=role,
        memberships=await _memberships(session, user),
    )


@router.post(
    "/register",
    response_model=TokenPair,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user and their first organization",
)
async def register(body: RegisterRequest, session: SessionDep, request: Request) -> TokenPair:
    existing = await session.scalar(select(User).where(User.email == body.email.lower()))
    if existing is not None:
        raise Conflict("An account with that email already exists")

    slug = _slugify(body.organization_name)
    if await session.scalar(select(Organization).where(Organization.slug == slug)):
        slug = f"{slug}-{uuid.uuid4().hex[:6]}"

    user = User(
        email=body.email.lower(),
        full_name=body.full_name,
        password_hash=hash_password(body.password),
    )
    organization = Organization(name=body.organization_name, slug=slug)
    session.add_all([user, organization])
    await session.flush()
    session.add(
        OrganizationMember(organization_id=organization.id, user_id=user.id, role=Role.org_admin)
    )
    await session.flush()
    await audit.record(
        session,
        None,
        "auth.register",
        "user",
        str(user.id),
        organization_id=organization.id,
        request=request,
        details={"email": user.email},
    )
    return await _issue(session, user, organization.id, Role.org_admin, request)


@router.post("/login", response_model=TokenPair, summary="Exchange credentials for tokens")
async def login(body: LoginRequest, session: SessionDep, request: Request) -> TokenPair:
    user = await session.scalar(select(User).where(User.email == body.email.lower()))
    # Same message either way — no account enumeration through the login endpoint.
    if user is None or not verify_password(body.password, user.password_hash):
        raise Unauthorized("Invalid email or password")
    if not user.is_active:
        raise Unauthorized("Account is disabled")

    memberships = (
        (
            await session.execute(
                select(OrganizationMember).where(OrganizationMember.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    if not memberships:
        raise Unauthorized("Account has no organization membership")

    chosen = next(
        (
            m
            for m in memberships
            if body.organization_id and m.organization_id == body.organization_id
        ),
        memberships[0],
    )
    if body.organization_id and chosen.organization_id != body.organization_id:
        raise Unauthorized("You are not a member of that organization")

    await audit.record(
        session,
        None,
        "auth.login",
        "user",
        str(user.id),
        organization_id=chosen.organization_id,
        request=request,
        details={"email": user.email},
    )
    return await _issue(session, user, chosen.organization_id, chosen.role, request)


@router.post("/refresh", response_model=TokenPair, summary="Rotate a refresh token")
async def refresh(body: RefreshRequest, session: SessionDep, request: Request) -> TokenPair:
    token_hash = sha256_hex(body.refresh_token)
    record = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    now = datetime.now(UTC)
    if record is None or record.revoked_at is not None or (as_utc(record.expires_at) or now) < now:
        raise Unauthorized("Refresh token is invalid or expired")

    record.revoked_at = now  # single use
    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise Unauthorized("Account is disabled")
    membership = await session.scalar(
        select(OrganizationMember).where(OrganizationMember.user_id == user.id)
    )
    if membership is None:
        raise Unauthorized("Account has no organization membership")
    return await _issue(session, user, membership.organization_id, membership.role, request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke a refresh token")
async def logout(body: RefreshRequest, session: SessionDep) -> None:
    record = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(body.refresh_token))
    )
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)


@router.get("/me", response_model=MeOut, summary="Current principal, role and permissions")
async def me(principal: PrincipalDep, session: SessionDep) -> MeOut:
    if principal.user is None:
        raise Unauthorized("API keys have no user profile; use a bearer token")
    return MeOut(
        user=UserOut.model_validate(principal.user),
        organization_id=principal.organization_id,
        role=principal.role,
        permissions=sorted(p for p, roles in ROLE_PERMISSIONS.items() if principal.role in roles),
        memberships=await _memberships(session, principal.user),
    )
