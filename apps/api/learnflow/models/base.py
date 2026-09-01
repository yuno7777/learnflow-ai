"""Declarative base, dialect-agnostic JSON types, and tenant mixins.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file provides the abstract base classes and mixins for all SQLAlchemy 2.0
ORM models in LearnFlow AI:
1. `JSONType`: Selects high-performance `JSONB` on PostgreSQL, falling back to
   plain `JSON` on SQLite for automated testing without code changes.
2. `UUIDPrimaryKey`: Mixin that gives tables a UUIDv4 primary key (`id`).
3. `Timestamped`: Mixin providing timezone-aware `created_at` and `updated_at` timestamps.
4. `TenantScoped`: Mixin establishing mandatory multi-tenancy. It adds an `organization_id`
   foreign key (with cascading deletes) and creates a composite index `(organization_id, created_at)`.
5. `Entity`: Base class for global models (e.g. `User`, `Organization`).
6. `TenantEntity`: Base class for all tenant-partitioned models (e.g. `Course`, `Document`, `Assessment`).

===============================================================================
HOW IT ENFORCES MULTI-TENANT SECURITY:
===============================================================================
By subclassing `TenantEntity`, a model is recognized by `TenantRepo` (`services/repository.py`).
If any code attempts to query a `TenantEntity` without an active tenant context,
`TenantRepo` raises `TenantIsolationError`, preventing data leaks by structural design.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Cross-dialect JSON mapping:
# In PostgreSQL: uses native binary JSONB (enables indexing and fast key lookups).
# In SQLite (unit tests): falls back to standard text JSON column.
JSONType = JSON().with_variant(JSONB, "postgresql")


def utcnow() -> datetime:
    """Returns the current timezone-aware UTC datetime."""
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Ensures a datetime object is timezone-aware in UTC.

    Fixes SQLite & driver quirks where TIMESTAMPTZ values return as naive datetimes,
    preventing runtime TypeErrors when comparing timestamps during authentication checks.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    """Root declarative base class for all database tables."""

    # Automatically serialize Python dicts and lists to JSONType columns
    type_annotation_map = {dict[str, Any]: JSONType, list[Any]: JSONType}


class UUIDPrimaryKey:
    """Mixin adding a UUIDv4 primary key column to tables."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Timestamped:
    """Mixin adding timezone-aware `created_at` and `updated_at` audit timestamps.

    Uses Python-side defaults (`default=utcnow`) alongside DB server defaults (`server_default=func.now()`)
    so that newly instantiated Python models have accurate UTC timestamps before being flushed to the database.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        onupdate=utcnow,
        nullable=False,
    )


class TenantScoped:
    """Mixin marking a table as strictly belonging to one organization tenant.

    Guarantees:
    1. An `organization_id` foreign key pointing to `organizations.id` with `ondelete="CASCADE"`.
    2. A composite database index `ix_{tablename}_org_created` over `(organization_id, created_at)`,
       ensuring fast filtered and sorted pagination queries per tenant.
    """

    @declared_attr
    def organization_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(
            Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
        )

    @declared_attr.directive
    def __table_args__(cls) -> tuple:  # noqa: N805
        table = cls.__tablename__  # type: ignore[attr-defined]
        return (Index(f"ix_{table}_org_created", "organization_id", "created_at"),)


class Entity(Base, UUIDPrimaryKey, Timestamped):
    """Abstract base class for global platform entities (e.g. User, Organization)."""

    __abstract__ = True


class TenantEntity(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    """Abstract base class for tenant-scoped domain entities.

    Inherited by: `Course`, `Document`, `Assessment`, `Submission`, `Workflow`, etc.
    """

    __abstract__ = True
