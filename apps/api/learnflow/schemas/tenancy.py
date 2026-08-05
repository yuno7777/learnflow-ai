from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from ..models import Role
from .common import ORMModel


class OrganizationOut(ORMModel):
    id: UUID
    name: str
    slug: str
    plan: str
    is_active: bool
    settings: dict[str, Any]
    created_at: datetime


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    plan: str | None = None
    settings: dict[str, Any] | None = None


class MemberOut(ORMModel):
    id: UUID
    user_id: UUID
    email: str
    full_name: str
    role: Role
    created_at: datetime


class MemberInvite(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    role: Role
    password: str | None = Field(
        default=None, description="Dev convenience: set an initial password for a new user"
    )


class MemberRoleUpdate(BaseModel):
    role: Role


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(min_length=2, max_length=80, pattern="^[a-z0-9-]+$")
    description: str | None = None


class WorkspaceOut(ORMModel):
    id: UUID
    name: str
    slug: str
    description: str | None
    created_at: datetime


class LearnerCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    workspace_id: UUID | None = None
    external_ref: str | None = None
    course_id: UUID | None = Field(
        default=None, description="Enrol immediately and fire the onboarding workflow"
    )
    profile: dict[str, Any] = Field(default_factory=dict)


class LearnerUpdate(BaseModel):
    full_name: str | None = None
    status: str | None = None
    workspace_id: UUID | None = None
    profile: dict[str, Any] | None = None


class LearnerOut(ORMModel):
    id: UUID
    email: str
    full_name: str
    status: str
    workspace_id: UUID | None
    external_ref: str | None
    last_active_at: datetime | None
    reminders_sent: int
    profile: dict[str, Any]
    created_at: datetime


class LearnerDetail(LearnerOut):
    enrollments: list[EnrollmentOut] = Field(default_factory=list)


class EnrollmentOut(ORMModel):
    id: UUID
    course_id: UUID
    course_title: str | None = None
    status: str
    progress: float
    completed_at: datetime | None
    created_at: datetime


LearnerDetail.model_rebuild()
