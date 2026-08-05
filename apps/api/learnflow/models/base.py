"""Declarative base plus the mixins every table in the system uses."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# JSONB on Postgres, plain JSON on SQLite (tests). Same Python interface.
JSONType = JSON().with_variant(JSONB, "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite (and some drivers) hand back naive datetimes for TIMESTAMPTZ columns.

    Everything we store is UTC, so attach the tzinfo rather than letting a naive/aware
    comparison raise TypeError deep inside an auth check.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONType, list[Any]: JSONType}


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Timestamped:
    # Python-side default (not only server_default) so every row is timezone-aware in the
    # same representation. Mixing DB-generated naive timestamps with aware ones makes range
    # filters silently wrong on SQLite and awkward on Postgres.
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
    """Marks a table as belonging to exactly one organization.

    The repository layer refuses to build a query against a TenantScoped model without
    an organization filter (see services/repository.py), so isolation is structural
    rather than a thing every endpoint has to remember.
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
    __abstract__ = True


class TenantEntity(Base, UUIDPrimaryKey, TenantScoped, Timestamped):
    __abstract__ = True
