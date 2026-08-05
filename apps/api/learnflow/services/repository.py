"""Tenant-scoped data access.

Every read/write of a `TenantScoped` model goes through `TenantRepo`, which refuses to
build a statement without the organization filter. That makes isolation a property of the
data layer instead of something each of ~60 endpoints has to remember.
"""

from __future__ import annotations

import uuid
from typing import Any, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import NotFound, TenantIsolationError
from ..models import TenantEntity, TenantScoped

T = TypeVar("T", bound=TenantEntity)

MAX_PAGE_SIZE = 100


class TenantRepo:
    def __init__(self, session: AsyncSession, organization_id: uuid.UUID) -> None:
        if organization_id is None:
            raise TenantIsolationError("Repository requires an organization context")
        self.session = session
        self.organization_id = organization_id

    # ---------------------------------------------------------------- statements
    def select(self, model: type[T]) -> Select:
        if not issubclass(model, TenantScoped):
            raise TenantIsolationError(f"{model.__name__} is not tenant-scoped; use the session")
        return select(model).where(model.organization_id == self.organization_id)

    # ---------------------------------------------------------------- reads
    async def get(self, model: type[T], obj_id: uuid.UUID | str) -> T:
        obj = await self.find(model, obj_id)
        if obj is None:
            # Deliberately identical to "belongs to another org" — no cross-tenant probing.
            raise NotFound(f"{model.__name__} not found")
        return obj

    async def find(self, model: type[T], obj_id: uuid.UUID | str) -> T | None:
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
        stmt = self.select(model)
        for clause in where or []:
            stmt = stmt.where(clause)

        total = await self.session.scalar(
            select(func.count()).select_from(stmt.order_by(None).subquery())
        )

        stmt = (
            stmt.order_by(self._order(model, sort)).limit(min(limit, MAX_PAGE_SIZE)).offset(offset)
        )
        for opt in options or []:
            stmt = stmt.options(opt)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return list(rows), int(total or 0)

    @staticmethod
    def _order(model: type[T], sort: str | None) -> Any:
        field = (sort or "-created_at").lstrip("-")
        column = getattr(model, field, None)
        if column is None:
            column = model.created_at  # unknown sort key falls back rather than 500s
        return column.desc() if (sort or "-created_at").startswith("-") else column.asc()

    # ---------------------------------------------------------------- writes
    def add(self, obj: T) -> T:
        """Stamps the tenant id so a caller can never create a row in another org."""
        obj.organization_id = self.organization_id
        self.session.add(obj)
        return obj

    async def delete(self, obj: T) -> None:
        if obj.organization_id != self.organization_id:
            raise TenantIsolationError("Refusing to delete an object from another organization")
        await self.session.delete(obj)

    async def flush(self) -> None:
        await self.session.flush()
