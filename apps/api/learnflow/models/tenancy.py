"""Users, organizations, workspaces, membership and roles."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Entity, TenantEntity


class Role(str, enum.Enum):
    platform_admin = "platform_admin"
    org_admin = "org_admin"
    instructor = "instructor"
    reviewer = "reviewer"
    support_agent = "support_agent"
    learner = "learner"


# Coarse capability model: a permission is granted if the member's role appears here.
# Kept as data rather than a permissions table — there is exactly one product using it.
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


class User(Entity):
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
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))

    organization: Mapped[Organization] = relationship(back_populates="workspaces")


class Learner(TenantEntity):
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
