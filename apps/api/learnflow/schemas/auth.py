from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from ..config import settings
from ..models import Role
from .common import ORMModel


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str = Field(min_length=2, max_length=200)
    organization_name: str = Field(min_length=2, max_length=200)

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        if len(v) < settings.password_min_length:
            raise ValueError(f"Password must be at least {settings.password_min_length} characters")
        if v.isalpha() or v.isdigit():
            raise ValueError("Password must mix letters with digits or symbols")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    organization_id: UUID | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class MembershipOut(ORMModel):
    organization_id: UUID
    organization_name: str
    organization_slug: str
    role: Role


class UserOut(ORMModel):
    id: UUID
    email: str
    full_name: str
    is_active: bool
    is_platform_admin: bool
    last_login_at: datetime | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
    organization_id: UUID
    role: Role
    memberships: list[MembershipOut]


class MeOut(BaseModel):
    user: UserOut
    organization_id: UUID
    role: Role
    learner_id: UUID | None = None
    permissions: list[str]
    memberships: list[MembershipOut]
