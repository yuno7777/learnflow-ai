"""Tenant-scoped Data Access Layer (DAL) repository.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file implements the `TenantRepo` class, which serves as the secure data access
layer for all tenant-scoped database interactions:
1. `select(model)`: Automatically injects `WHERE organization_id = :org_id` into all queries.
2. `get(model, id)`: Fetches an entity by ID, raising `NotFound` (404) if it belongs to
   another organization or does not exist (preventing cross-tenant ID probing).
3. `list(model, limit, offset, sort)`: Executes paginated queries returning `(rows, total_count)`.
4. `add(obj)`: Automatically stamps `organization_id = self.organization_id` on new models.
5. `delete(obj)`: Strictly validates tenant ownership before issuing a DELETE.

===============================================================================
CRITICAL MULTI-TENANT SECURITY PRINCIPLE:
===============================================================================
Instead of relying on developers to manually remember `.where(Model.organization_id == current_org)`
across 60+ API endpoints, `TenantRepo` enforces it structurally. Calling `repo.select(model)`
on any model that does NOT inherit from `TenantScoped` raises `TenantIsolationError`.
"""

from __future__ import annotations

import uuid
from typing import Any, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import NotFound, TenantIsolationError
from ..models import TenantEntity, TenantScoped

T = TypeVar("T", bound=TenantEntity)

MAX_PAGE_SIZE = 100  # Enforces maximum page size limit on list queries


class TenantRepo:
    """Encapsulates SQLAlchemy session queries bound strictly to one organization tenant."""

    def __init__(self, session: AsyncSession, organization_id: uuid.UUID) -> None:
        if organization_id is None:
            raise TenantIsolationError("Repository requires an organization context")
        self.session = session
        self.organization_id = organization_id

    # -------------------------------------------------------------------------
    # Statement Construction
    # -------------------------------------------------------------------------
    def select(self, model: type[T]) -> Select:
        """Constructs a SQLAlchemy SELECT statement automatically filtered by tenant ID."""
        if not issubclass(model, TenantScoped):
            raise TenantIsolationError(f"{model.__name__} is not tenant-scoped; use the session")
        return select(model).where(model.organization_id == self.organization_id)

    # -------------------------------------------------------------------------
    # Read Operations
    # -------------------------------------------------------------------------
    async def get(self, model: type[T], obj_id: uuid.UUID | str) -> T:
        """Fetches entity by primary key.

        Throws:
            NotFound: If entity does not exist OR belongs to another tenant.
                      (Returning 404 instead of 403 prevents attackers from enumerating valid IDs).
        """
        obj = await self.find(model, obj_id)
        if obj is None:
            raise NotFound(f"{model.__name__} not found")
        return obj

    async def find(self, model: type[T], obj_id: uuid.UUID | str) -> T | None:
        """Finds entity by ID, returning None if not found or outside tenant boundary."""
        if isinstance(obj_id, str):
            try:
                obj_id = uuid.UUID(obj_id)
            except ValueError:
                return None
        result = await self.session.execute(self.select(model).where(model.id == obj_id))
        return result.scalar_one_or_none()

    async def list(
        self,
        model: type[T],
        *,
        where: list[Any] | None = None,
        sort: str | None = None,
        limit: int = 25,
        offset: int = 0,
        options: list[Any] | None = None,
    ) -> tuple[list[T], int]:
        """Performs paginated query with total count and dynamic sorting.

        Args:
            model: TenantScoped SQLAlchemy model.
            where: Additional filter expressions.
            sort: Sort string (e.g. "-created_at" for DESC, "title" for ASC).
            limit: Number of records to return.
            offset: Offset for pagination.
            options: SQLAlchemy loader options (e.g. `selectinload`).

        Returns:
            tuple[list[T], int]: (matching_entities, total_count)
        """
        stmt = self.select(model)
        for clause in where or []:
            stmt = stmt.where(clause)

        # Count total matching rows in tenant
        total = await self.session.scalar(
            select(func.count()).select_from(stmt.order_by(None).subquery())
        )

        # Apply sorting, limits, and offsets
        stmt = (
            stmt.order_by(self._order(model, sort)).limit(min(limit, MAX_PAGE_SIZE)).offset(offset)
        )
        for opt in options or []:
            stmt = stmt.options(opt)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return list(rows), int(total or 0)

    @staticmethod
    def _order(model: type[T], sort: str | None) -> Any:
        """Translates sort string (e.g. '-created_at') into a SQLAlchemy column ordering."""
        field = (sort or "-created_at").lstrip("-")
        column = getattr(model, field, None)
        if column is None:
            column = model.created_at  # Fallback to created_at if invalid sort column passed
        return column.desc() if (sort or "-created_at").startswith("-") else column.asc()

    # -------------------------------------------------------------------------
    # Write Operations
    # -------------------------------------------------------------------------
    def add(self, obj: T) -> T:
        """Attaches new object to session and stamps current organization_id onto it."""
        obj.organization_id = self.organization_id
        self.session.add(obj)
        return obj

    async def delete(self, obj: T) -> None:
        """Deletes an entity after verifying it belongs to this tenant."""
        if obj.organization_id != self.organization_id:
            raise TenantIsolationError("Refusing to delete an object from another organization")
        await self.session.delete(obj)

    async def flush(self) -> None:
        """Flushes pending SQL operations to the database."""
        await self.session.flush()
