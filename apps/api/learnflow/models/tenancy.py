"""Users, organizations, workspaces, membership, and role-based access control (RBAC).

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file defines the identity and tenancy database models:
1. `Role`: Enum of supported platform roles (`platform_admin`, `org_admin`, `instructor`,
   `reviewer`, `support_agent`, `learner`).
2. `ROLE_PERMISSIONS`: A capability lookup mapping permission strings to allowed roles.
3. `User`: Global user account model (email, password hash, platform admin status).
4. `Organization`: Tenant container model (name, slug, billing plan, settings).
5. `OrganizationMember`: Many-to-many relationship linking a `User` to an `Organization`
   with a specific `Role`.
6. `Workspace`: Optional logical grouping within an organization (e.g. departments).
7. `Learner`: Represents a student/learner profile within an organization.
   (Linked to a `User` when the learner is given interactive portal login credentials).

===============================================================================
HOW IT CONNECTS TO THE REST OF THE PROJECT:
===============================================================================
- `deps.py`: Resolves `Principal` and enforces permission checks using `ROLE_PERMISSIONS`.
- `routers/auth.py`: Creates `User`, `Organization`, and `OrganizationMember(role=org_admin)`
  upon registration.
- `routers/learners.py` & `routers/organizations.py`: Manage learner invitations, member roles,
  and tenant settings.
- `models/learning.py`: `Enrollment` and `Submission` reference `learners.id`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Entity, TenantEntity

# -----------------------------------------------------------------------------
# Role Definitions & Permissions Map
# -----------------------------------------------------------------------------


class Role(str, enum.Enum):
    """System roles representing distinct user personas and permission levels."""

    platform_admin = "platform_admin"  # Superuser with global access across all tenants
    org_admin = "org_admin"  # Tenant administrator (manages members, billing, API keys)
    instructor = "instructor"  # Course creator (authors content, generates quizzes)
    reviewer = "reviewer"  # Human evaluator (grades flagged assessments)
    support_agent = "support_agent"  # Helpdesk operator (manages tickets)
    learner = "learner"  # Student portal user (read-only courses, submits quizzes)


# Coarse capability model: maps fine-grained action strings to sets of allowed roles.
# Used by `deps.require("permission:name")` to guard API endpoints.
ROLE_PERMISSIONS: dict[str, set[Role]] = {
    "org:manage": {Role.platform_admin, Role.org_admin},
    "member:manage": {Role.platform_admin, Role.org_admin},
    "learner:manage": {Role.platform_admin, Role.org_admin, Role.instructor},
    "document:write": {Role.platform_admin, Role.org_admin, Role.instructor},
    "course:write": {Role.platform_admin, Role.org_admin, Role.instructor},
    "course:publish": {Role.platform_admin, Role.org_admin, Role.instructor},
    "assessment:write": {Role.platform_admin, Role.org_admin, Role.instructor},
    "evaluation:approve": {Role.platform_admin, Role.org_admin, Role.instructor, Role.reviewer},
    "workflow:write": {Role.platform_admin, Role.org_admin},
    "workflow:run": {Role.platform_admin, Role.org_admin, Role.instructor},
    "ticket:manage": {Role.platform_admin, Role.org_admin, Role.support_agent},
    "analytics:read": {
        Role.platform_admin,
        Role.org_admin,
        Role.instructor,
        Role.reviewer,
        Role.support_agent,
    },
    "apikey:manage": {Role.platform_admin, Role.org_admin},
    "audit:read": {Role.platform_admin, Role.org_admin},
    "rag:query": {
        Role.platform_admin,
        Role.org_admin,
        Role.instructor,
        Role.reviewer,
        Role.support_agent,
        Role.learner,
    },
}

RoleEnum = SAEnum(
    Role, native_enum=False, length=32, values_callable=lambda e: [m.value for m in e]
)


# -----------------------------------------------------------------------------
# Database Models
# -----------------------------------------------------------------------------


class User(Entity):
    """Global user table. A user exists globally and can belong to multiple organizations."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[OrganizationMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class Organization(Entity):
    """Tenant organization boundary. All courses, documents, and workflows belong to an org."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(40), default="free", nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    members: Mapped[list[OrganizationMember]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    workspaces: Mapped[list[Workspace]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class OrganizationMember(Entity):
    """Association table linking a User to an Organization with an assigned Role.

    Enforces uniqueness on `(organization_id, user_id)` so a user has one role per organization.
    """

    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_member_org_user"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[Role] = mapped_column(RoleEnum, nullable=False)

    user: Mapped[User] = relationship(back_populates="memberships", lazy="joined")
    organization: Mapped[Organization] = relationship(back_populates="members", lazy="joined")


class Workspace(TenantEntity):
    """Optional departmental/team division within an organization."""

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))

    organization: Mapped[Organization] = relationship(back_populates="workspaces")


class Learner(TenantEntity):
    """Student profile within an organization.

    May exist without a user login (e.g. imported from HRIS/CSV).
    When portal access is provisioned, `user_id` is linked to a `User` record.
    """

    __tablename__ = "learners"

    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), default="invited", nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(120))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    reminders_sent: Mapped[int] = mapped_column(default=0, nullable=False)
    profile: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
